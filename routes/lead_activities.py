import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from auth import get_current_user
from database import get_db
from models import Contact, Employee, LeadActivity
from schemas.lead_activity import (
    Coordinates,
    LeadActivityCreate,
    LeadActivityResponse,
    LeadActivityUpdate,
)

router = APIRouter(
    prefix="/contacts", tags=["lead-activities"], dependencies=[Depends(get_current_user)]
)


def _task_state(activity: LeadActivity) -> tuple[str, int]:
    """(state, days overdue) for an activity; log entries and finished tasks are always "done"."""
    if activity.status == "done":
        return "done", 0
    today = datetime.date.today()
    if activity.scheduled_date is None:
        return "pending", 0
    if activity.scheduled_date < today:
        return "overdue", (today - activity.scheduled_date).days
    return ("due_today" if activity.scheduled_date == today else "pending"), 0


def _response(activity: LeadActivity) -> LeadActivityResponse:
    response = LeadActivityResponse.model_validate(activity, from_attributes=True)
    response.created_by_name = activity.creator.name if activity.creator else None
    response.assigned_to_name = activity.assignee.name if activity.assignee else None
    response.completed_by_name = activity.completer.name if activity.completer else None
    if activity.latitude is not None and activity.longitude is not None:
        response.coordinates = Coordinates(lat=float(activity.latitude), lng=float(activity.longitude))
    response.task_state, response.overdue_days = _task_state(activity)
    return response


def _contact_or_404(db: Session, contact_id: int) -> Contact:
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if not contact or contact.deleted_at:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


def _task_or_404(db: Session, contact_id: int, activity_id: int) -> LeadActivity:
    activity = (
        db.query(LeadActivity)
        .filter(LeadActivity.id == activity_id, LeadActivity.contact_id == contact_id)
        .first()
    )
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    return activity


@router.get("/{contact_id}/activities", response_model=list[LeadActivityResponse])
def list_activities(contact_id: int, db: Session = Depends(get_db)):
    """Activity log and follow-up tasks for a contact, newest first."""
    _contact_or_404(db, contact_id)
    rows = (
        db.query(LeadActivity)
        .options(
            joinedload(LeadActivity.creator),
            joinedload(LeadActivity.assignee),
            joinedload(LeadActivity.completer),
        )
        .filter(LeadActivity.contact_id == contact_id)
        .order_by(LeadActivity.created_at.desc(), LeadActivity.id.desc())
        .all()
    )
    return [_response(row) for row in rows]


@router.post("/{contact_id}/activities", response_model=LeadActivityResponse, status_code=201)
def create_activity(
    contact_id: int,
    body: LeadActivityCreate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Log an activity (done now, server time) or schedule a follow-up for a staff member."""
    _contact_or_404(db, contact_id)
    if body.is_task:
        assignee = db.query(Employee).filter(Employee.id == body.assigned_to).first()
        if not assignee or assignee.deleted_at or not assignee.is_active:
            raise HTTPException(status_code=400, detail="Assigned employee not found")

    activity = LeadActivity(
        contact_id=contact_id,
        type=body.type,
        notes=(body.notes or "").strip() or None,
        created_by=current_user.id,
        status="pending" if body.is_task else "done",
        scheduled_date=body.scheduled_date,
        scheduled_time=body.scheduled_time,
        assigned_to=body.assigned_to,
        completed_at=None if body.is_task else datetime.datetime.now(),
        completed_by=None if body.is_task else current_user.id,
        latitude=body.coordinates.lat if body.coordinates else None,
        longitude=body.coordinates.lng if body.coordinates else None,
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return _response(activity)


@router.patch("/{contact_id}/activities/{activity_id}", response_model=LeadActivityResponse)
def update_activity(
    contact_id: int,
    activity_id: int,
    body: LeadActivityUpdate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Mark a pending follow-up done (server sets the time and the user), or reschedule it."""
    _contact_or_404(db, contact_id)
    activity = _task_or_404(db, contact_id, activity_id)
    if activity.scheduled_date is None:
        raise HTTPException(status_code=409, detail="Only scheduled follow-ups can be changed")
    if activity.status == "done":
        raise HTTPException(status_code=409, detail="Follow-up is already done")

    if body.status == "done":
        activity.status = "done"
        activity.completed_at = datetime.datetime.now()
        activity.completed_by = current_user.id
    else:
        activity.scheduled_date = body.scheduled_date
        activity.scheduled_time = body.scheduled_time  # None clears the time (date-only task)
    db.commit()
    db.refresh(activity)
    return _response(activity)
