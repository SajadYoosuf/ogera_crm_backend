import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from auth import get_current_user
from database import get_db
import settings_catalog as catalog
from contact_access import get_visible_contact
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


# Roles that see and manage every employee's tasks; everyone else only their own.
TASK_MANAGER_ROLES = {"admin", "manager", "sales_head"}


# Admin and manager monitor the team; follow-ups are never assigned to them (see rule.md)
MONITORING_ROLES = {"admin", "manager"}


def can_manage_tasks(user: Employee) -> bool:
    return user.role in TASK_MANAGER_ROLES


def task_state(activity: LeadActivity, now: datetime.datetime | None = None) -> tuple[str, int]:
    """(state, days overdue) for an activity; log entries and finished tasks are always "done".

    A task with a time is overdue once that time has passed; a date-only task is due until the day ends.
    """
    if activity.status == "done":
        return "done", 0
    now = now or datetime.datetime.now()
    today = now.date()
    if activity.scheduled_date is None:
        return "pending", 0
    if activity.scheduled_date < today:
        return "overdue", (today - activity.scheduled_date).days
    if activity.scheduled_date == today:
        if activity.scheduled_time is not None and activity.scheduled_time < now.time():
            return "overdue", 0
        return "due_today", 0
    return "pending", 0


def activity_response(activity: LeadActivity) -> LeadActivityResponse:
    response = LeadActivityResponse.model_validate(activity, from_attributes=True)
    response.created_by_name = activity.creator.name if activity.creator else None
    response.assigned_to_name = activity.assignee.name if activity.assignee else None
    response.completed_by_name = activity.completer.name if activity.completer else None
    if activity.latitude is not None and activity.longitude is not None:
        response.coordinates = Coordinates(lat=float(activity.latitude), lng=float(activity.longitude))
    response.task_state, response.overdue_days = task_state(activity)
    return response


def _contact_or_404(db: Session, contact_id: int, user: Employee) -> Contact:
    """The contact if the user may see it; 404 otherwise (same as a missing contact)."""
    return get_visible_contact(db, user, contact_id)


def _task_or_404(db: Session, contact_id: int, activity_id: int) -> LeadActivity:
    activity = (
        db.query(LeadActivity)
        .filter(LeadActivity.id == activity_id, LeadActivity.contact_id == contact_id)
        .first()
    )
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    return activity


@router.get("/{contact_id}/activities", response_model=list[LeadActivityResponse], summary="List activities of a contact")
def list_activities(
    contact_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """The activity log and follow-up tasks of one contact, newest first. Each entry shows who created it, who it is assigned to, and who completed it."""
    _contact_or_404(db, contact_id, current_user)
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
    return [activity_response(row) for row in rows]


@router.post("/{contact_id}/activities", response_model=LeadActivityResponse, status_code=201, summary="Log an activity or schedule a follow-up")
def create_activity(
    contact_id: int,
    body: LeadActivityCreate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """A logged activity is saved as done at the server time.
    A follow-up (with a date) is assigned to a staff member and stays pending until it is completed.
    Follow-ups cannot be assigned to an admin or manager (400).
    """
    _contact_or_404(db, contact_id, current_user)
    catalog.ensure_allowed(db, catalog.ACTIVITY_TYPE, body.type)
    if body.is_task:
        assignee = db.query(Employee).filter(Employee.id == body.assigned_to).first()
        if not assignee or assignee.deleted_at or not assignee.is_active:
            raise HTTPException(status_code=400, detail="Assigned employee not found")
        if assignee.role in MONITORING_ROLES:
            raise HTTPException(status_code=400, detail="Follow-ups cannot be assigned to an admin or manager")

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
    return activity_response(activity)


@router.patch("/{contact_id}/activities/{activity_id}", response_model=LeadActivityResponse, summary="Complete or reschedule a follow-up")
def update_activity(
    contact_id: int,
    activity_id: int,
    body: LeadActivityUpdate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Marks a pending follow-up done (the server records the time and the user), or changes its date and time."""
    _contact_or_404(db, contact_id, current_user)
    activity = _task_or_404(db, contact_id, activity_id)
    if activity.scheduled_date is None:
        raise HTTPException(status_code=409, detail="Only scheduled follow-ups can be changed")
    if activity.status == "done":
        raise HTTPException(status_code=409, detail="Follow-up is already done")
    if not can_manage_tasks(current_user) and activity.assigned_to != current_user.id:
        raise HTTPException(status_code=403, detail="This follow-up is assigned to someone else")

    if body.status == "done":
        now = datetime.datetime.now()
        activity.status = "done"
        activity.completed_at = now
        activity.completed_by = current_user.id
        # The outcome is the record of what happened, so it goes into the activity log
        db.add(LeadActivity(
            contact_id=contact_id,
            type=activity.type,
            notes=body.outcome.strip(),
            status="done",
            completed_at=now,
            completed_by=current_user.id,
            created_by=current_user.id,
        ))
    else:
        activity.scheduled_date = body.scheduled_date
        activity.scheduled_time = body.scheduled_time  # None clears the time (date-only task)
    db.commit()
    db.refresh(activity)
    return activity_response(activity)
