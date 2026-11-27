"""
Notification service — creates in-app notifications and dispatches FCM after commit.

HOW IT WORKS:
    1. A route handler creates notification rows in its transaction.
    2. create_notification() inserts one row in the `notifications` table (in-app inbox).
    3. Route-provided BackgroundTasks dispatch FCM after the response transaction commits.
    4. The scheduler calls run_48h_rule() and run_daily_digest() on a background timer.

NEVER RAISES:
    Like mailer.py, every function here catches its own exceptions. A notification
    failure must never crash a contact creation, follow-up update, or any other action.
"""
import logging
import threading
from datetime import datetime, timedelta, timezone
from fastapi import BackgroundTasks
from sqlalchemy import and_, event, func as sqlfunc, or_, select
from sqlalchemy.orm import Session
from database import SessionLocal, get_session_for_schema
from models.notification import Notification
from models.employee import Employee
from models.role import Role
from models.fcm_device import FcmDevice
from models.interaction import Interaction
from models.lead import Lead
from models.contact import Contact
from business_time import ist_day_bounds, ist_now, today_ist
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

def _is_invalid_token_error(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return "unregistered" in name or "registrationtokennotregistered" in name or "senderidmismatch" in name


def send_fcm(token: str | None, title: str, message: str, data: dict | None = None, on_invalid_token=None) -> bool:
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
        if _is_invalid_token_error(exc) and on_invalid_token:
            on_invalid_token(token)
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
    background_tasks: BackgroundTasks | None = None,
) -> Notification | None:
    """
    Insert one notification row for one recipient.

    INPUTS:
        db           — SQLAlchemy session (caller owns the commit)
        recipient_id — employee who should receive this notification
        notif_type   — short key, e.g. "contact_created", "inactivity", "daily_digest"
        title        — headline (shown in push notification and bell panel)
        message      — full sentence
        entity_type  — "contact", "task", or "followup" (None for digest)
        entity_id    — id of the entity so the UI can link to it

    FCM delivery is queued as a post-response background task when the caller
    provides `background_tasks`. It never runs inside the caller's transaction.
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

        # Read the tenant schema stored by get_session_for_schema so FCM can open
        # its own schema-scoped session after the HTTP response is sent.
        tenant_schema: str | None = db.info.get("_tenant_schema")

        if background_tasks is not None:
            background_tasks.add_task(
                dispatch_notification_push,
                recipient_id,
                notif_type,
                title,
                message,
                entity_type,
                entity_id,
                tenant_schema,
            )
        else:
            db.info.setdefault(PENDING_PUSHES_KEY, []).append(
                (recipient_id, notif_type, title, message, entity_type, entity_id, tenant_schema)
            )
        return notif
    except Exception as exc:
        logger.error("create_notification failed for recipient_id=%s: %s", recipient_id, exc)
        return None


def dispatch_notification_push(
    recipient_id: int,
    notif_type: str,
    title: str,
    message: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    tenant_schema: str | None = None,
) -> None:
    """Send FCM after commit without holding a database transaction during I/O."""
    if not tenant_schema:
        logger.warning("FCM skipped for recipient_id=%s: no tenant_schema provided", recipient_id)
        return
    from firebase_init import is_ready
    if not is_ready():
        return

    try:
        with get_session_for_schema(tenant_schema) as db:
            recipient = db.get(Employee, recipient_id)
            devices = db.query(FcmDevice).filter(
                FcmDevice.employee_id == recipient_id
            ).all()
            tokens = {device.token for device in devices}
            if recipient and recipient.fcm_token:
                tokens.add(recipient.fcm_token)
    except Exception as exc:
        logger.error("FCM recipient lookup failed for recipient_id=%s: %s", recipient_id, exc)
        return

    payload = {
        "type": notif_type,
        "entity_type": entity_type or "",
        "entity_id": str(entity_id or ""),
    }
    invalid_tokens: set[str] = set()
    for token in tokens:
        send_fcm(
            token,
            title,
            message,
            payload,
            on_invalid_token=invalid_tokens.add,
        )

    if invalid_tokens:
        try:
            with get_session_for_schema(tenant_schema) as cleanup_db:
                for token in invalid_tokens:
                    _remove_invalid_token(cleanup_db, token)
                cleanup_db.commit()
        except Exception as exc:
            logger.error("Invalid FCM token cleanup failed: %s", exc)


PENDING_PUSHES_KEY = "pending_pushes"


def _dispatch_pending(pushes: list) -> None:
    for push in pushes:
        dispatch_notification_push(*push)


@event.listens_for(Session, "after_commit")
def _send_pushes_after_commit(session: Session) -> None:
    pending = session.info.pop(PENDING_PUSHES_KEY, None)
    if pending:
        threading.Thread(target=_dispatch_pending, args=(pending,), daemon=True).start()


@event.listens_for(Session, "after_rollback")
def _drop_pushes_after_rollback(session: Session) -> None:
    session.info.pop(PENDING_PUSHES_KEY, None)


def _remove_invalid_token(db: Session, token: str | None) -> None:
    if not token:
        return
    db.query(FcmDevice).filter(FcmDevice.token == token).delete(synchronize_session=False)
    db.query(Employee).filter(Employee.fcm_token == token).update({Employee.fcm_token: None})


def notify_task_completed(
    db: Session,
    task,
    completed_by: Employee,
    background_tasks: BackgroundTasks | None = None,
) -> None:
    """Notify the assignee and leadership after a task becomes completed."""
    task_title = task.title or "Task"
    title = f"Task completed: {task_title}"
    msg = f'{completed_by.name} completed the task "{task_title}".'
    recipients = {task.assigned_to}
    leaders = db.query(Employee).filter(
        Employee.role_id.in_(select(Role.id).where(Role.slug.in_(["admin", "manager"]))),
        Employee.is_active == 1,
        Employee.deleted_at.is_(None),
    ).all()
    recipients.update(emp.id for emp in leaders)
    for recipient_id in recipients:
        if recipient_id and recipient_id != completed_by.id:
            create_notification(
                db,
                recipient_id,
                "task_completed",
                title,
                msg,
                "task",
                task.id,
                background_tasks,
            )


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
    background_tasks: BackgroundTasks | None = None,
) -> None:
    """
    Send the same notification to every active employee who holds any of the given roles.

    Example:
        notify_roles(db, ["admin", "manager"], "contact_created", "New lead: Ali", "...")
    sends one notification per admin and per manager currently in the system.
    """
    try:
        employees = db.query(Employee).filter(
            Employee.role_id.in_(select(Role.id).where(Role.slug.in_(roles))),
            Employee.is_active == 1,
            Employee.deleted_at.is_(None),
        ).all()
        for emp in employees:
            create_notification(
                db,
                emp.id,
                notif_type,
                title,
                message,
                entity_type,
                entity_id,
                background_tasks,
            )
    except Exception as exc:
        logger.error("notify_roles failed (roles=%s): %s", roles, exc)


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------

def run_48h_rule(db: Session) -> int:
    """
    Find contacts with no recent activity and fire inactivity reminders.

    RULE:
        If a contact has assigned_to set AND no logged activity (an `interactions`
        row with kind='log') within the last NOTIF_INACTIVITY_HOURS hours, send a reminder to:
          - the assigned sales executive
          - all admin and manager employees
        Dedup: skip if this (recipient, "inactivity", contact_id) was already sent in the last 24h.

    RETURNS: number of notifications inserted this run.
    """
    hours = config.NOTIF_INACTIVITY_HOURS
    # Interaction times are written as UTC (see routes/contact_interactions.py), so the cutoff is UTC too.
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    sent = 0
    try:
        # Contacts that have had recent activity — we exclude these
        # select() builds an explicit SQL SELECT, which IN() accepts without the
        # SAWarning. Tasks also carry logged_at, so only kind='log' counts as activity.
        active_contact_ids = (
            select(Interaction.entity_id)
            .where(
                Interaction.entity_type == "contact",
                Interaction.kind == "log",
                Interaction.logged_at >= cutoff,
            )
            .distinct()
        )
        # Also exclude contacts whose follow-ups have been completed recently
        # (completing a follow-up writes a log interaction, so this is handled above)

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
            Employee.role_id.in_(select(Role.id).where(Role.slug.in_(["admin", "manager"]))),
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
    # interactions.due_at holds naive UTC, so "today" is the UTC span of the IST calendar day.
    start, end = ist_day_bounds(today_ist())
    sent = 0
    try:
        live_contacts = select(Contact.id).where(Contact.deleted_at.is_(None))
        live_leads = select(Lead.id).where(Lead.deleted_at.is_(None))
        rows = (
            db.query(
                Interaction.assigned_to,
                sqlfunc.count(Interaction.id).label("cnt"),
            )
            .filter(
                Interaction.kind == "task",
                Interaction.status.in_(["todo", "inprogress"]),
                Interaction.assigned_to.isnot(None),
                Interaction.due_at >= start,
                Interaction.due_at < end,
                or_(
                    and_(Interaction.entity_type == "contact", Interaction.entity_id.in_(live_contacts)),
                    and_(Interaction.entity_type == "lead", Interaction.entity_id.in_(live_leads)),
                ),
            )
            .group_by(Interaction.assigned_to)
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
