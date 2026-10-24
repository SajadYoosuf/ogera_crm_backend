"""Recent contact events for the mobile activity log picker.

Route: GET /contacts/recent-activity?limit=20

This router is registered BEFORE the contacts router in main.py. If it came
after, GET /contacts/{contact_id} would match "recent-activity" first and fail
with 422, because FastAPI tries routes in the order they were added.

The rules (who sees which events) live in services/recent_activity.py.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models import Employee
from schemas.activity_log import RecentActivityItem
from services.recent_activity import list_recent_activity

router = APIRouter(
    prefix="/contacts", tags=["activity-log"], dependencies=[Depends(get_current_user)]
)


@router.get(
    "/recent-activity",
    response_model=list[RecentActivityItem],
    summary="Recent contacts created or assigned, newest first",
)
def get_recent_activity(
    limit: int = Query(20, ge=1, le=50, description="How many events to return (1-50)"),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Contact-created and contact-assigned events the signed-in employee may see."""
    return list_recent_activity(db, current_user, limit)
