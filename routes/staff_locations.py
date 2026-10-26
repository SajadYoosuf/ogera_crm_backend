"""
Staff location streaming — receives live GPS pings from the mobile foreground service.

WHAT THIS FILE DOES:
    POST /staff/location     — upsert the current employee's live GPS position.
    GET  /staff/location/me  — read back the employee's own last known position.

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
import math
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from auth import get_current_user, require_roles
from database import get_db
from models import Employee
from models.staff_location import StaffLocation
from models.staff_location_history import StaffLocationHistory

router = APIRouter(prefix="/staff", tags=["staff-location"], dependencies=[Depends(get_current_user)])

# How many seconds must pass before we accept another ping from the same employee.
THROTTLE_SECONDS = 30


class LocationBody(BaseModel):
    latitude: float
    longitude: float


class LocationResponse(BaseModel):
    latitude: float
    longitude: float
    recorded_at: datetime.datetime


@router.get(
    "/location/me",
    response_model=LocationResponse | None,
    summary="My last known GPS position",
)
def get_my_location(
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Returns the signed-in employee's most recent stored position, or null when
    no ping has been received yet. Only the caller's own row is ever read, so
    one employee can never see another employee's location (no IDOR).
    """
    row = (
        db.query(StaffLocation)
        .filter(StaffLocation.employee_id == current_user.id)
        .first()
    )
    if row is None:
        return None
    return LocationResponse(
        latitude=row.latitude,
        longitude=row.longitude,
        recorded_at=row.recorded_at,
    )


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
    db.add(StaffLocationHistory(
        employee_id=current_user.id,
        latitude=body.latitude,
        longitude=body.longitude,
        recorded_at=now,
    ))
    db.commit()
    return {"ok": True, "throttled": False}


class LiveStaffResponse(BaseModel):
    userId: int
    name: str
    phone: str | None
    latitude: float | None
    longitude: float | None
    address: str | None = None
    recordedAt: datetime.datetime | None
    state: str
    colorIndex: int


class RoutePointResponse(BaseModel):
    latitude: float
    longitude: float
    address: str | None = None
    recordedAt: datetime.datetime
    state: str


class RouteResponse(BaseModel):
    points: list[RoutePointResponse]
    stops: list[dict] = []
    distanceMeters: float
    firstSeen: datetime.datetime | None = None
    lastSeen: datetime.datetime | None = None


def _manager(current_user: Employee = Depends(require_roles("admin", "manager"))) -> Employee:
    return current_user


@router.get("/locations/live", response_model=list[LiveStaffResponse], dependencies=[Depends(_manager)])
def get_live_staff(db: Session = Depends(get_db)):
    rows = (
        db.query(StaffLocation, Employee)
        .join(Employee, Employee.id == StaffLocation.employee_id)
        .filter(Employee.is_active == 1, Employee.deleted_at.is_(None))
        .order_by(Employee.name.asc())
        .all()
    )
    now = datetime.datetime.utcnow()
    result = []
    for index, (location, employee) in enumerate(rows):
        age = (now - location.recorded_at).total_seconds()
        state = "connection_lost" if age > 180 else "moving"
        result.append(LiveStaffResponse(
            userId=employee.id,
            name=employee.name or "Ogera user",
            phone=employee.phone,
            latitude=location.latitude,
            longitude=location.longitude,
            recordedAt=location.recorded_at,
            state=state,
            colorIndex=index,
        ))
    return result


@router.get("/locations/history", response_model=RouteResponse, dependencies=[Depends(_manager)])
def get_staff_history(userId: int, date: str, db: Session = Depends(get_db)):
    try:
        day = datetime.date.fromisoformat(date)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD") from error
    start = datetime.datetime.combine(day, datetime.time.min)
    end = start + datetime.timedelta(days=1)
    rows = (
        db.query(StaffLocationHistory)
        .filter(
            StaffLocationHistory.employee_id == userId,
            StaffLocationHistory.recorded_at >= start,
            StaffLocationHistory.recorded_at < end,
        )
        .order_by(StaffLocationHistory.recorded_at.asc())
        .all()
    )
    points = [RoutePointResponse(
        latitude=row.latitude,
        longitude=row.longitude,
        recordedAt=row.recorded_at,
        state="moving",
    ) for row in rows]
    distance = 0.0
    for previous, current in zip(points, points[1:]):
        lat1, lat2 = math.radians(previous.latitude), math.radians(current.latitude)
        dlat = lat2 - lat1
        dlon = math.radians(current.longitude - previous.longitude)
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        distance += 6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return RouteResponse(
        points=points,
        distanceMeters=round(distance, 1),
        firstSeen=points[0].recordedAt if points else None,
        lastSeen=points[-1].recordedAt if points else None,
    )
