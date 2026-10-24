"""
Notification service — the single place that creates in-app notifications and sends FCM push.

HOW IT WORKS:
    1. A route handler calls notify_roles() or create_notification() right after a DB commit.
    2. create_notification() inserts one row in the `notifications` table (in-app inbox).
    3. It also calls send_fcm() to send a push to the recipient's device if they have an FCM token.
    4. The scheduler calls run_48h_rule() and run_daily_digest() on a background timer.

NEVER RAISES:
    Like mailer.py, every function here catches its own exceptions. A notification
    failure must never crash a contact creation, follow-up update, or any other action.
"""
import logging
from datetime import timedelta
from sqlalchemy import func as sqlfunc, select
from sqlalchemy.orm import Session
from models.notification import Notification
from models.employee import Employee
from models.activity_log import ActivityLog
from models.followup_schedule import FollowupSchedule
from models.contact import Contact
from business_time import ist_now
import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Deduplication helper
# ---------------------------------------------------------------------------

def check_dedup(
    db: Session,
    recipient_id: int,
    notif_type: str,
    entity_id: int | None,
    hours: int = 24,
) -> bool:
    """
    Return True if this exact notification was already sent to this recipient recently.

    WHY DEDUP?
        The inactivity rule runs every hour. Without dedup the same "no activity" reminder
        would fire once per hour instead of once per day for the same contact.

    True  = already sent within `hours` → skip.
    False = not seen recently → safe to send.
    """
    if entity_id is None:
        return False
    cutoff = ist_now() - timedelta(hours=hours)
    exists = db.query(Notification).filter(
        Notification.recipient_id == recipient_id,
        Notification.type == notif_type,
        Notification.entity_id == entity_id,
        Notification.created_at >= cutoff,
    ).first()
    return exists is not None


# ---------------------------------------------------------------------------
# FCM push helper
# ---------------------------------------------------------------------------

def send_fcm(token: str | None, title: str, message: str, data: dict | None = None) -> bool:
    """
    Send a Firebase Cloud Messaging push notification to one device token.

    WHY NOT RAISE?
        A push failure must never block the main user action. We log a warning and
        return False so the caller can continue.

    INPUT:  device/browser FCM token, title, body text, optional data payload
    OUTPUT: True if sent, False on any error or missing token
    """
    if not token:
        return False
    try:
        from firebase_admin import messaging
        from firebase_init import is_ready
        if not is_ready():
            return False
        msg = messaging.Message(
            notification=messaging.Notification(title=title, body=message),
            data={k: str(v) for k, v in (data or {}).items()},
            token=token,
        )
        messaging.send(msg)
        return True
    except Exception as exc:
        logger.warning("FCM send failed (token ...%s): %s", (token or "")[-6:], exc)
        return False


# ---------------------------------------------------------------------------
# Core create function
# ---------------------------------------------------------------------------

def create_notification(
    db: Session,
    recipient_id: int,
    notif_type: str,
    title: str,
    message: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
) -> Notification | None:
    """
    Insert one notification row for one recipient and attempt an FCM push.

    INPUTS:
        db           — SQLAlchemy session (caller owns the commit)
        recipient_id — employee who should receive this notification
        notif_type   — short key, e.g. "contact_created", "inactivity", "daily_digest"
        title        — headline (shown in push notification and bell panel)
        message      — full sentence
        entity_type  — "contact", "task", or "followup" (None for digest)
        entity_id    — id of the entity so the UI can link to it

    NEVER RAISES — a notification failure must not crash the caller.
    """
    try:
        notif = Notification(
            recipient_id=recipient_id,
            type=notif_type,
            title=title,
            message=message,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        db.add(notif)

        # Attempt FCM push — read the recipient's token from the session
        recipient = db.get(Employee, recipient_id)
        if recipient and recipient.fcm_token:
            send_fcm(
                recipient.fcm_token,
                title,
                message,
                {
                    "type": notif_type,
                    "entity_type": entity_type or "",
                    "entity_id": str(entity_id or ""),
                },
            )
        return notif
    except Exception as exc:
        logger.error("create_notification failed for recipient_id=%s: %s", recipient_id, exc)
        return None


# ---------------------------------------------------------------------------
# Fan-out helper
# ---------------------------------------------------------------------------

def notify_roles(
    db: Session,
    roles: list[str],
    notif_type: str,
    title: str,
    message: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
) -> None:
    """
    Send the same notification to every active employee who holds any of the given roles.

    Example:
        notify_roles(db, ["admin", "manager"], "contact_created", "New lead: Ali", "...")
    sends one notification per admin and per manager currently in the system.
    """
    try:
        employees = db.query(Employee).filter(
            Employee.role.in_(roles),
            Employee.is_active == 1,
            Employee.deleted_at.is_(None),
        ).all()
        for emp in employees:
            create_notification(db, emp.id, notif_type, title, message, entity_type, entity_id)
    except Exception as exc:
        logger.error("notify_roles failed (roles=%s): %s", roles, exc)


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------

def run_48h_rule(db: Session) -> int:
    """
    Find contacts with no recent activity and fire inactivity reminders.

    RULE:
        If a contact has assigned_to set AND no ActivityLog row within the last
        NOTIF_INACTIVITY_HOURS hours, send a reminder to:
          - the assigned sales executive
          - all admin and manager employees
        Dedup: skip if this (recipient, "inactivity", contact_id) was already sent in the last 24h.

    RETURNS: number of notifications inserted this run.
    """
    hours = config.NOTIF_INACTIVITY_HOURS
    cutoff = ist_now() - timedelta(hours=hours)
    sent = 0
    try:
        # Contacts that have had recent activity — we exclude these
        # select() builds an explicit SQL SELECT, which IN() accepts without the
        # SAWarning. The NULL filter matters: "x NOT IN (..., NULL)" is never true
        # in SQL, so a NULL contact_id would silently stop every reminder.
        active_contact_ids = (
            select(ActivityLog.contact_id)
            .where(
                ActivityLog.logged_at >= cutoff,
                ActivityLog.contact_id.isnot(None),
            )
            .distinct()
        )
        # Also exclude contacts whose follow-ups have been completed recently
        # (completing a follow-up writes an ActivityLog row, so this is handled above)

        contacts = (
            db.query(Contact)
            .filter(
                Contact.assigned_to.isnot(None),
                Contact.deleted_at.is_(None),
                ~Contact.id.in_(active_contact_ids),
            )
            .all()
        )

        admins_managers = db.query(Employee).filter(
            Employee.role.in_(["admin", "manager"]),
            Employee.is_active == 1,
            Employee.deleted_at.is_(None),
        ).all()

        for contact in contacts:
            title = f"No activity: {contact.name}"
            msg = f"{contact.name} ({contact.contact_type}) has had no logged activity in the last {hours} hours."

            # Notify the assigned sales executive
            if not check_dedup(db, contact.assigned_to, "inactivity", contact.id, 24):
                create_notification(db, contact.assigned_to, "inactivity", title, msg, "contact", contact.id)
                sent += 1

            # Notify admin / manager (skip if they are the assigned person too)
            for emp in admins_managers:
                if emp.id != contact.assigned_to and not check_dedup(db, emp.id, "inactivity", contact.id, 24):
                    create_notification(db, emp.id, "inactivity", title, msg, "contact", contact.id)
                    sent += 1

        if sent:
            db.commit()
        logger.info("Inactivity rule run: %d notification(s) sent", sent)
    except Exception as exc:
        logger.error("run_48h_rule failed: %s", exc)
    return sent


def run_daily_digest(db: Session) -> int:
    """
    Send each employee a morning summary of their tasks due today.

    Called once per day at NOTIF_DIGEST_HOUR (IST) by the scheduler.
    Only sends if the employee has at least one open task scheduled for today.

    RETURNS: number of notifications inserted.
    """
    if not config.NOTIF_DIGEST_ENABLED:
        logger.info("Daily digest disabled via NOTIF_DIGEST_ENABLED=false")
        return 0
    today = ist_now().date()
    sent = 0
    try:
        rows = (
            db.query(
                FollowupSchedule.assigned_to,
                sqlfunc.count(FollowupSchedule.id).label("cnt"),
            )
            .filter(
                sqlfunc.date(FollowupSchedule.due_at) == today,
                FollowupSchedule.status.in_(["todo", "inprogress"]),
            )
            .group_by(FollowupSchedule.assigned_to)
            .all()
        )

        for assigned_to, count in rows:
            s = "s" if count != 1 else ""
            title = f"You have {count} task{s} today"
            msg = f"Good morning! You have {count} follow-up task{s} scheduled for today."
            create_notification(db, assigned_to, "daily_digest", title, msg)
            sent += 1

        if sent:
            db.commit()
        logger.info("Daily digest sent to %d employee(s)", sent)
    except Exception as exc:
        logger.error("run_daily_digest failed: %s", exc)
    return sent
