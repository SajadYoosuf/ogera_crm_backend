import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from auth import get_current_user
from contact_access import ensure_can_assign, get_visible_contact, visible_assignee_ids
from role_access import ensure_can_schedule_for
from database import get_db
import config
from models import Contact, Employee, FollowupSchedule
from models.followup_reschedule import FollowupReschedule
from schemas.followup_schedule import (
    FollowupScheduleCreate,
    FollowupScheduleResponse,
    FollowupScheduleUpdate,
)
from schemas.activity_log import ActivityLogCreate
from models.activity_log import ActivityLog
import settings_catalog as catalog
from business_time import ist_now

router = APIRouter(
    prefix="/contacts", tags=["followups"], dependencies=[Depends(get_current_user)]
)

MONITORING_ROLES = {"admin", "manager"}
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
    get_visible_contact(db, current_user, contact_id)
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
    get_visible_contact(db, current_user, contact_id)
    assignee = db.query(Employee).filter(Employee.id == body.assigned_to).first()
    if not assignee or assignee.deleted_at or not assignee.is_active:
        raise HTTPException(status_code=400, detail="Assigned employee not found or inactive")
    if assignee.role in MONITORING_ROLES:
        raise HTTPException(status_code=400, detail="Follow-ups cannot be assigned to an admin or manager")
    ensure_can_schedule_for(current_user, body.assigned_to)
    ensure_can_assign(db, current_user, body.assigned_to)
    fs = FollowupSchedule(
        contact_id=contact_id,
        assigned_to=body.assigned_to,
        title=(body.title or "").strip() or None,
        due_at=body.due_at,
        status="todo",
        priority=body.priority,
        channel=checked_channel(db, body.channel),
        notes=(body.notes or "").strip() or None,
    )
    db.add(fs)
    db.commit()
    db.refresh(fs)
    db.refresh(fs, attribute_names=["contact", "assignee"])
    return _followup_response(fs)


@router.patch("/{contact_id}/followups/{followup_id}", response_model=FollowupScheduleResponse, summary="Complete, reschedule or skip a follow-up")
def update_followup(
    contact_id: int,
    followup_id: int,
    body: FollowupScheduleUpdate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Mark done (status=done + outcome) → creates activity_log row.
    outcome in (no_answer, not_now) → auto-schedules a retry follow-up.
    Reschedule (due_at) or skip (status=skipped) are the other operations.
    """
    get_visible_contact(db, current_user, contact_id)
    fs = _get_followup_or_404(db, contact_id, followup_id)

    if fs.status == "completed":
        raise HTTPException(status_code=409, detail="Follow-up is already completed")
    visible = visible_assignee_ids(db, current_user)
    if visible is not None and fs.assigned_to not in visible:
        raise HTTPException(status_code=403, detail="This follow-up is assigned to someone else")

    if body.status == "completed":
        fs.status = "completed"
        if fs.contact_id:
            log = ActivityLog(
                contact_id=fs.contact_id,
                done_by=current_user.id,
                activity_type=LEGACY_CHANNELS.get(fs.channel or "", fs.channel or "other"),
                outcome=body.outcome,
                notes=(body.notes or "").strip() or None,
                followup_id=fs.id,
            )
            db.add(log)
            if body.outcome in RETRY_OUTCOMES:
                retry = FollowupSchedule(
                    contact_id=fs.contact_id,
                    assigned_to=fs.assigned_to,
                    due_at=ist_now() + datetime.timedelta(days=config.RETRY_FOLLOWUP_DAYS),
                    status="todo",
                    priority=fs.priority,
                    channel=fs.channel,
                )
                db.add(retry)

    elif body.status in ("todo", "inprogress"):
        fs.status = body.status

    else:  # reschedule
        db.add(FollowupReschedule(
            followup_id=fs.id, contact_id=fs.contact_id, from_due_at=fs.due_at, to_due_at=body.due_at, changed_by=current_user.id,
        ))
        fs.due_at = body.due_at

    db.commit()
    db.refresh(fs)
    db.refresh(fs, attribute_names=["contact", "assignee"])
    return _followup_response(fs)
