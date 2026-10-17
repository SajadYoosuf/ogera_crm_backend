import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from auth import get_current_user
from contact_access import get_visible_contact
from database import get_db
import config
from models import Contact, Employee, FollowupSchedule
from schemas.followup_schedule import (
    FollowupScheduleCreate,
    FollowupScheduleResponse,
    FollowupScheduleUpdate,
)
from schemas.activity_log import ActivityLogCreate
from models.activity_log import ActivityLog

router = APIRouter(
    prefix="/contacts", tags=["followups"], dependencies=[Depends(get_current_user)]
)

MONITORING_ROLES = {"admin", "manager"}
TASK_MANAGER_ROLES = {"admin", "manager", "sales_head"}
RETRY_OUTCOMES = {"no_answer", "not_now"}
CHANNEL_TO_ACTIVITY_TYPE = {"call": "phone_call", "visit": "field_visit", "whatsapp": "whatsapp"}


def _task_state(fs: FollowupSchedule, now: datetime.datetime | None = None) -> tuple[str, int]:
    if fs.status != "pending":
        return fs.status, 0
    now = now or datetime.datetime.now()
    due = fs.due_at
    if due < now - datetime.timedelta(days=0):
        today = now.date()
        due_date = due.date()
        if due_date < today:
            return "overdue", (today - due_date).days
        if due_date == today:
            return "due_today", 0
    return "pending", 0


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
    fs = FollowupSchedule(
        contact_id=contact_id,
        assigned_to=body.assigned_to,
        title=(body.title or "").strip() or None,
        due_at=body.due_at,
        status="pending",
        channel=body.channel,
        notes=(body.notes or "").strip() or None,
        whatsapp_reminder=body.whatsapp_reminder,
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

    if fs.status != "pending":
        raise HTTPException(status_code=409, detail=f"Follow-up is already {fs.status}")
    if current_user.role not in TASK_MANAGER_ROLES and fs.assigned_to != current_user.id:
        raise HTTPException(status_code=403, detail="This follow-up is assigned to someone else")

    if body.status == "done":
        fs.status = "done"
        # Write activity_log row
        log = ActivityLog(
            contact_id=contact_id,
            done_by=current_user.id,
            activity_type=CHANNEL_TO_ACTIVITY_TYPE.get(fs.channel, fs.channel),
            outcome=body.outcome,
            notes=(body.notes or "").strip() or None,
            followup_id=fs.id,
        )
        db.add(log)
        # Auto-retry on no_answer / not_now
        if body.outcome in RETRY_OUTCOMES:
            retry = FollowupSchedule(
                contact_id=contact_id,
                assigned_to=fs.assigned_to,
                due_at=datetime.datetime.now() + datetime.timedelta(days=config.RETRY_FOLLOWUP_DAYS),
                status="pending",
                channel=fs.channel,
            )
            db.add(retry)

    elif body.status == "skipped":
        fs.status = "skipped"

    else:  # reschedule
        fs.due_at = body.due_at

    db.commit()
    db.refresh(fs)
    db.refresh(fs, attribute_names=["contact", "assignee"])
    return _followup_response(fs)
