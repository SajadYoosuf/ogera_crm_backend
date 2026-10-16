import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from auth import get_current_user
from contact_access import get_visible_contact
from database import get_db
from models import Employee
from models.activity_log import ActivityLog
from schemas.activity_log import ActivityLogCreate, ActivityLogResponse, Coordinates
import settings_catalog as catalog

router = APIRouter(
    prefix="/contacts", tags=["activity-log"], dependencies=[Depends(get_current_user)]
)

MONITORING_ROLES = {"admin", "manager"}
TASK_MANAGER_ROLES = {"admin", "manager", "sales_head"}


def can_manage_tasks(user: Employee) -> bool:
    return user.role in TASK_MANAGER_ROLES


def _activity_response(log: ActivityLog) -> ActivityLogResponse:
    r = ActivityLogResponse.model_validate(log, from_attributes=True)
    r.done_by_name = log.doer.name if log.doer else None
    if log.latitude is not None and log.longitude is not None:
        r.coordinates = Coordinates(lat=float(log.latitude), lng=float(log.longitude))
    return r


@router.get("/{contact_id}/activities", response_model=list[ActivityLogResponse], summary="List activity log for a contact")
def list_activities(
    contact_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    get_visible_contact(db, current_user, contact_id)
    rows = (
        db.query(ActivityLog)
        .options(joinedload(ActivityLog.doer))
        .filter(ActivityLog.contact_id == contact_id)
        .order_by(ActivityLog.logged_at.desc(), ActivityLog.id.desc())
        .all()
    )
    return [_activity_response(r) for r in rows]


@router.post("/{contact_id}/activities", response_model=ActivityLogResponse, status_code=201, summary="Log an ad-hoc activity")
def create_activity(
    contact_id: int,
    body: ActivityLogCreate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Log an interaction that was not pre-scheduled (no followup_id). For completing a scheduled follow-up, use PATCH /followups/{id}."""
    get_visible_contact(db, current_user, contact_id)
    catalog.ensure_allowed(db, catalog.ACTIVITY_TYPE, body.activity_type)
    log = ActivityLog(
        contact_id=contact_id,
        done_by=current_user.id,
        activity_type=body.activity_type,
        outcome=body.outcome,
        notes=(body.notes or "").strip() or None,
        followup_id=None,
        latitude=body.coordinates.lat if body.coordinates else None,
        longitude=body.coordinates.lng if body.coordinates else None,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    db.refresh(log, attribute_names=["doer"])
    return _activity_response(log)
