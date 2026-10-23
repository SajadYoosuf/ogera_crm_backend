import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from auth import get_current_user
from contact_lookup import get_live_contact
from database import get_db
import config
from models import Contact, Employee, FollowupSchedule
from models.followup_reschedule import FollowupReschedule
from models.followup_edit_log import FollowupEditLog
from schemas.followup_schedule import (
    FollowupScheduleCreate,
    FollowupScheduleResponse,
    FollowupScheduleUpdate,
)
from schemas.activity_log import ActivityLogCreate
from models.activity_log import ActivityLog
import settings_catalog as catalog
from business_time import ist_now

# Roles that monitor but do not execute work. They can reschedule any follow-up regardless of
# who created it, because they need to manage their team's schedule.
MONITORING_ROLES = {"admin", "manager"}

router = APIRouter(
    prefix="/contacts", tags=["followups"], dependencies=[Depends(get_current_user)]
)

RETRY_OUTCOMES = {"no_answer", "not_now"}
# Older clients send these short names. They stay as stored names so existing rows and apps keep matching.
LEGACY_CHANNELS = {"call": "phone_call", "visit": "field_visit"}
STORED_CHANNELS = {activity_type: name for name, activity_type in LEGACY_CHANNELS.items()}
NOT_SCHEDULABLE = {"internal_note"}


def checked_channel(db: Session, channel: str | None) -> str | None:
    """The stored channel for a follow-up's activity type. The type must be active in Settings -> Activity types."""
    if channel is None:
        return None
    activity_type = LEGACY_CHANNELS.get(channel, channel)
    if activity_type in NOT_SCHEDULABLE:
        raise HTTPException(status_code=400, detail="Internal notes cannot be scheduled")
    catalog.ensure_allowed(db, catalog.ACTIVITY_TYPE, activity_type)
    return STORED_CHANNELS.get(activity_type, activity_type)


def _task_state(fs: FollowupSchedule, now: datetime.datetime | None = None) -> tuple[str, int]:
    if fs.status == "completed":
        return "completed", 0
    now = now or ist_now()
    today = now.date()
    due_date = fs.due_at.date()
    if due_date < today:
        return "overdue", (today - due_date).days
    if due_date == today:
        return "due_today", 0
    return fs.status, 0  # "todo" or "inprogress"


def _followup_response(fs: FollowupSchedule) -> FollowupScheduleResponse:
    r = FollowupScheduleResponse.model_validate(fs, from_attributes=True)
    r.assigned_to_name = fs.assignee.name if fs.assignee else None
    r.created_by_id = fs.created_by
    if fs.contact:
        r.contact_name = fs.contact.name
        r.contact_phone = fs.contact.phone
        r.contact_type = fs.contact.contact_type
    r.task_state, r.overdue_days = _task_state(fs)
    return r


def _get_followup_or_404(db: Session, contact_id: int, followup_id: int) -> FollowupSchedule:
    fs = db.query(FollowupSchedule).filter(
        FollowupSchedule.id == followup_id,
        FollowupSchedule.contact_id == contact_id,
    ).first()
    if not fs:
        raise HTTPException(status_code=404, detail="Follow-up not found")
    return fs


@router.get("/{contact_id}/followups", response_model=list[FollowupScheduleResponse], summary="List follow-ups for a contact")
def list_followups(
    contact_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    get_live_contact(db, contact_id)
    rows = (
        db.query(FollowupSchedule)
        .options(joinedload(FollowupSchedule.contact), joinedload(FollowupSchedule.assignee))
        .filter(FollowupSchedule.contact_id == contact_id)
        .order_by(FollowupSchedule.due_at.desc())
        .all()
    )
    return [_followup_response(r) for r in rows]


@router.post("/{contact_id}/followups", response_model=FollowupScheduleResponse, status_code=201, summary="Schedule a follow-up")
def create_followup(
    contact_id: int,
    body: FollowupScheduleCreate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    get_live_contact(db, contact_id)
    assignee = db.query(Employee).filter(Employee.id == body.assigned_to).first()
    if not assignee or assignee.deleted_at or not assignee.is_active:
        raise HTTPException(status_code=400, detail="Assigned employee not found or inactive")
    fs = FollowupSchedule(
        contact_id=contact_id,
        assigned_to=body.assigned_to,
        title=(body.title or "").strip() or None,
        due_at=body.due_at,
        status="todo",
        priority=body.priority,
        channel=checked_channel(db, body.channel),
        notes=(body.notes or "").strip() or None,
        created_by=current_user.id,
    )
    db.add(fs)
    db.commit()
    db.refresh(fs)
    db.refresh(fs, attribute_names=["contact", "assignee"])
    return _followup_response(fs)


def _log_edit(db: Session, fs: FollowupSchedule, changed_by: int, field_name: str, old_value, new_value) -> None:
    """Write one followup_edit_log row if the value actually changed. Values compared as strings."""
    old_str = str(old_value) if old_value is not None else None
    new_str = str(new_value) if new_value is not None else None
    if old_str != new_str:
        db.add(FollowupEditLog(
            followup_id=fs.id,
            contact_id=fs.contact_id,
            changed_by=changed_by,
            field_name=field_name,
            old_value=old_str,
            new_value=new_str,
        ))


@router.patch("/{contact_id}/followups/{followup_id}", response_model=FollowupScheduleResponse, summary="Complete, reschedule or skip a follow-up")
def update_followup(
    contact_id: int,
    followup_id: int,
    body: FollowupScheduleUpdate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Mark done (status=completed + outcome) → creates activity_log row.
    actual_channel overrides the logged activity type at completion time
    (e.g. was scheduled as phone_call but actually used whatsapp).
    outcome in (no_answer, not_now) → auto-schedules a retry follow-up if none is already open.
    Reschedule (due_at) → creator-only (admin/manager can always reschedule).
    Status change (todo / inprogress / pending) → any employee.
    Every field change is recorded in followup_edit_log.
    """
    get_live_contact(db, contact_id)
    fs = _get_followup_or_404(db, contact_id, followup_id)

    if fs.status == "completed":
        raise HTTPException(status_code=409, detail="Follow-up is already completed")

    if body.status == "completed":
        # Resolve the channel: use actual_channel if provided, otherwise fall back to the
        # scheduled channel, translating legacy short names to full activity_type names.
        actual_ch = body.actual_channel or fs.channel
        logged_type = LEGACY_CHANNELS.get(actual_ch or "", actual_ch or "other")
        if body.actual_channel and body.actual_channel != fs.channel:
            _log_edit(db, fs, current_user.id, "channel", fs.channel, body.actual_channel)

        fs.status = "completed"
        _log_edit(db, fs, current_user.id, "status", "open", "completed")

        if fs.contact_id:
            log = ActivityLog(
                contact_id=fs.contact_id,
                done_by=current_user.id,
                activity_type=logged_type,
                outcome=body.outcome,
                notes=(body.notes or "").strip() or None,
                followup_id=fs.id,
            )
            db.add(log)

            # Auto-schedule a retry only if no open follow-up already exists for this contact+assignee.
            # Prevents an unbounded chain of pending tasks from repeated no_answer completions.
            # db.flush() ensures the completed status is visible to the count query below —
            # without it the query might count this follow-up as still open.
            if body.outcome in RETRY_OUTCOMES:
                db.flush()
                existing_open = db.query(FollowupSchedule).filter(
                    FollowupSchedule.contact_id == fs.contact_id,
                    FollowupSchedule.assigned_to == fs.assigned_to,
                    FollowupSchedule.status.in_(("todo", "inprogress", "pending")),
                ).count()
                if existing_open == 0:
                    retry = FollowupSchedule(
                        contact_id=fs.contact_id,
                        assigned_to=fs.assigned_to,
                        due_at=ist_now() + datetime.timedelta(days=config.RETRY_FOLLOWUP_DAYS),
                        status="todo",
                        priority=fs.priority,
                        channel=fs.channel,
                        created_by=current_user.id,
                    )
                    db.add(retry)

    elif body.status in ("todo", "inprogress", "pending"):
        _log_edit(db, fs, current_user.id, "status", fs.status, body.status)
        fs.status = body.status

    else:  # reschedule (body.due_at is set)
        # Only the creator of this follow-up can reschedule it.
        # Admin and manager can always reschedule regardless of ownership.
        if (
            fs.created_by is not None
            and fs.created_by != current_user.id
            and current_user.role not in MONITORING_ROLES
        ):
            raise HTTPException(status_code=403, detail="Only the employee who scheduled this follow-up can reschedule it")

        db.add(FollowupReschedule(
            followup_id=fs.id, contact_id=fs.contact_id, from_due_at=fs.due_at, to_due_at=body.due_at, changed_by=current_user.id,
        ))
        _log_edit(db, fs, current_user.id, "due_at", fs.due_at.isoformat(), body.due_at.isoformat())
        fs.due_at = body.due_at

    db.commit()
    db.refresh(fs)
    db.refresh(fs, attribute_names=["contact", "assignee"])
    return _followup_response(fs)
