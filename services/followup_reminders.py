"""
Follow-up reminders for lead tasks (interactions with kind='task', entity_type='lead').

Two rules, both notify the task's assignee and link the notification to the lead:

    followup_due      — at 09:00 IST, for every open task due today (IST calendar day).
                        At most one per lead per assignee per day.
    followup_overdue  — hourly, for every open task more than 24 hours past due.
                        At most one per lead per assignee, ever.

"Open" = status is not 'completed'. Each tenant schema is processed separately; the
tenant list comes from public.tenants (status='active').

All times are compared as naive UTC, which is what interactions.due_at and
notifications.created_at hold for the API's writes.
"""
import datetime
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from business_time import ist_day_bounds, today_ist
from models.interaction import Interaction
from models.lead import Lead
from models.notification import Notification
from services.notification_service import create_notification

logger = logging.getLogger(__name__)

OVERDUE_AFTER = datetime.timedelta(hours=24)


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _naive_utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(datetime.timezone.utc).replace(tzinfo=None)


def _open_lead_tasks(db: Session):
    return (
        select(Interaction, Lead)
        .join(Lead, Lead.id == Interaction.entity_id)
        .where(Interaction.entity_type == "lead", Interaction.kind == "task")
        .where(Interaction.status != "completed")
        .where(Interaction.assigned_to.is_not(None), Interaction.due_at.is_not(None))
        .where(Lead.deleted_at.is_(None))
    )


def _already_sent(db: Session, recipient_id: int, notif_type: str, lead_id: int, since: datetime.datetime | None) -> bool:
    q = select(Notification.id).where(
        Notification.recipient_id == recipient_id,
        Notification.type == notif_type,
        Notification.entity_type == "lead",
        Notification.entity_id == lead_id,
    )
    if since is not None:
        q = q.where(Notification.created_at >= since)
    return db.scalar(q.limit(1)) is not None


def _lead_name(lead: Lead) -> str:
    return " ".join(p for p in (lead.first_name, lead.last_name) if p) or f"Lead #{lead.id}"


def send_due_today(db: Session, day: datetime.date | None = None) -> int:
    """Notify assignees about open tasks due on `day` (IST). Returns notifications created."""
    day = day or today_ist()
    start, end = ist_day_bounds(day)
    sent = 0
    seen: set[tuple[int, int]] = set()
    for task, lead in db.execute(_open_lead_tasks(db)).all():
        due = _naive_utc(task.due_at)
        if not (start <= due < end):
            continue
        key = (task.assigned_to, lead.id)
        if key in seen or _already_sent(db, task.assigned_to, "followup_due", lead.id, since=start):
            continue
        seen.add(key)
        create_notification(db, task.assigned_to, "followup_due", "Follow-up due today",
                            f"{task.title or 'Follow-up'} — {_lead_name(lead)}", "lead", lead.id)
        sent += 1
    db.commit()
    return sent


def send_overdue(db: Session, now: datetime.datetime | None = None) -> int:
    """Notify assignees about open tasks more than 24 h past due. Returns notifications created."""
    now = _naive_utc(now) if now else _utc_now()
    sent = 0
    seen: set[tuple[int, int]] = set()
    for task, lead in db.execute(_open_lead_tasks(db)).all():
        if _naive_utc(task.due_at) > now - OVERDUE_AFTER:
            continue
        key = (task.assigned_to, lead.id)
        if key in seen or _already_sent(db, task.assigned_to, "followup_overdue", lead.id, since=None):
            continue
        seen.add(key)
        create_notification(db, task.assigned_to, "followup_overdue", "Follow-up overdue",
                            f"{task.title or 'Follow-up'} — {_lead_name(lead)} is more than a day overdue.",
                            "lead", lead.id)
        sent += 1
    db.commit()
    return sent


def _active_schemas() -> list[str]:
    from database import get_public_db_ctx
    from models.tenant import Tenant
    with get_public_db_ctx() as db:
        return list(db.scalars(select(Tenant.schema_name).where(Tenant.status == "active")).all())


def run_for_all_tenants(job) -> None:
    """Run `job(db)` once per active tenant schema. One tenant's failure never stops the others."""
    from database import get_session_for_schema
    try:
        schemas = _active_schemas()
    except Exception as exc:  # noqa: BLE001 — a scheduler job must not crash the process
        logger.error("followup reminders — could not list tenants: %s", exc)
        return
    for schema in schemas:
        try:
            with get_session_for_schema(schema) as db:
                count = job(db)
                if count:
                    logger.info("followup reminders — %s: %d sent by %s", schema, count, job.__name__)
        except Exception as exc:  # noqa: BLE001
            logger.error("followup reminders — %s failed: %s", schema, exc)
