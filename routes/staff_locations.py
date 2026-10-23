"""
Staff location streaming — receives live GPS pings from the mobile foreground service.

WHAT THIS FILE DOES:
    POST /staff/location — upsert the current employee's live GPS position.

THROTTLE:
    The server ignores a ping that arrives within THROTTLE_SECONDS of the previous one.
    This prevents the database from being hammered when the device sends many pings
    quickly (e.g. driving). The mobile app sends whenever it moves 50 m, so the
    throttle only kicks in on rapid movement.

WHO CAN USE IT:
    Any active signed-in employee. The mobile foreground service calls this continuously
    while a field session is open (after check-in, before checkout).
"""
import datetime
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from auth import get_current_user
from database import get_db
from models import Employee
from models.staff_location import StaffLocation

router = APIRouter(prefix="/staff", tags=["staff-location"], dependencies=[Depends(get_current_user)])

# How many seconds must pass before we accept another ping from the same employee.
THROTTLE_SECONDS = 30


class LocationBody(BaseModel):
    latitude: float
    longitude: float


@router.post(
    "/location",
    summary="Update my live GPS position",
)
def update_location(
    body: LocationBody,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Upserts the employee's live position.

    - First call: inserts a new row.
    - Subsequent calls: overwrites the existing row, but only when at least
      THROTTLE_SECONDS have elapsed since the last accepted ping.
    - Returns `throttled: true` (HTTP 200) when the ping is ignored. The mobile
      app can safely discard this response — there is nothing to retry.
    """
    now = datetime.datetime.utcnow()
    existing = (
        db.query(StaffLocation)
        .filter(StaffLocation.employee_id == current_user.id)
        .first()
    )

    if existing is not None:
        elapsed = (now - existing.recorded_at).total_seconds()
        if elapsed < THROTTLE_SECONDS:
            return {"ok": True, "throttled": True}
        existing.latitude = body.latitude
        existing.longitude = body.longitude
        existing.recorded_at = now
    else:
        db.add(StaffLocation(
            employee_id=current_user.id,
            latitude=body.latitude,
            longitude=body.longitude,
            recorded_at=now,
        ))

    db.commit()
    return {"ok": True, "throttled": False}
