import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from auth import get_current_user
from database import get_db
from models import Contact, Employee, LeadActivity
from schemas.lead_activity import LeadActivityComplete, LeadActivityCreate, LeadActivityResponse

router = APIRouter(
    prefix="/contacts", tags=["lead-activities"], dependencies=[Depends(get_current_user)]
)


def _task_state(activity: LeadActivity) -> str:
    if activity.status == "done":
        return "done"
    if activity.due_at is None:
        return "pending"
    today = datetime.date.today()
    if activity.due_at.date() < today:
        return "overdue"
    return "due_today" if activity.due_at.date() == today else "pending"


def _response(activity: LeadActivity) -> LeadActivityResponse:
    response = LeadActivityResponse.model_validate(activity, from_attributes=True)
    response.created_by_name = activity.creator.name if activity.creator else None
    response.task_state = _task_state(activity)
    return response


def _contact_or_404(db: Session, contact_id: int) -> Contact:
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if not contact or contact.deleted_at:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


@router.get("/{contact_id}/activities", response_model=list[LeadActivityResponse])
def list_activities(contact_id: int, db: Session = Depends(get_db)):
    """Activity log and tasks for a contact: open tasks first (earliest due first), then newest done."""
    _contact_or_404(db, contact_id)
    rows = (
        db.query(LeadActivity)
        .options(joinedload(LeadActivity.creator))
        .filter(LeadActivity.contact_id == contact_id)
        .all()
    )
    far_future = datetime.datetime.max
    pending = sorted((r for r in rows if r.status == "pending"), key=lambda r: (r.due_at or far_future, r.id))
    done = sorted((r for r in rows if r.status == "done"), key=lambda r: (r.done_at or r.created_at, r.id), reverse=True)
    return [_response(r) for r in [*pending, *done]]


@router.post("/{contact_id}/activities", response_model=LeadActivityResponse, status_code=201)
def create_activity(
    contact_id: int,
    body: LeadActivityCreate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Log something already done, or schedule a task by sending `due_at`."""
    _contact_or_404(db, contact_id)
    scheduled = body.due_at is not None
    activity = LeadActivity(
        contact_id=contact_id,
        created_by=current_user.id,
        status="pending" if scheduled else "done",
        done_at=None if scheduled else datetime.datetime.now(),
        **body.model_dump(),
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return _response(activity)


@router.patch("/{contact_id}/activities/{activity_id}", response_model=LeadActivityResponse)
def complete_activity(
    contact_id: int,
    activity_id: int,
    body: LeadActivityComplete,
    db: Session = Depends(get_db),
):
    """Mark a pending task done."""
    _contact_or_404(db, contact_id)
    activity = (
        db.query(LeadActivity)
        .filter(LeadActivity.id == activity_id, LeadActivity.contact_id == contact_id)
        .first()
    )
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    if activity.status == "done":
        raise HTTPException(status_code=409, detail="Activity is already done")
    outcome = (body.outcome or "").strip()
    if outcome:
        activity.notes = f"{activity.notes}\n\nOutcome: {outcome}" if activity.notes else f"Outcome: {outcome}"
    activity.status = "done"
    activity.done_at = datetime.datetime.now()
    db.commit()
    db.refresh(activity)
    return _response(activity)
