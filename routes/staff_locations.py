import datetime
import math
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session
from auth import get_current_user, require_roles
from database import get_db
from models import Employee, StaffLocation, StaffLocationState
from schemas import RoleEnum

router = APIRouter(prefix="/staff/locations", tags=["staff-locations"])

# Live map and route history are for admins and managers only.
view_staff_locations = require_roles(RoleEnum.admin, RoleEnum.manager)

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
STALE_AFTER = datetime.timedelta(minutes=15)  # no point for this long -> connection lost
STOP_MIN_DURATION = datetime.timedelta(minutes=10)
STOP_RADIUS_METERS = 60.0
COLOR_COUNT = 12
REPORTABLE_STATES = {"location_disabled", "location_enabled", "signed_out"}


class PointIn(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    recordedAt: datetime.datetime
    isMoving: bool = True
    accuracy: float | None = None
    speed: float | None = None
    batteryLevel: int | None = Field(default=None, ge=0, le=100)


class PointsIn(BaseModel):
    points: list[PointIn] = Field(max_length=200)


class StateIn(BaseModel):
    state: str


def _utc_naive(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(datetime.timezone.utc).replace(tzinfo=None)


def _iso(value: datetime.datetime | None) -> str | None:
    return value.replace(tzinfo=datetime.timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


@router.post("", status_code=201, summary="Upload location points")
def upload_points(
    body: PointsIn,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """The phone uploads a batch of GPS points. A point with the same employee and time as an existing one is ignored, so retries are safe. Requires sign-in."""
    if not body.points:
        return {"saved": 0}
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    rows = []
    for point in body.points:
        recorded = _utc_naive(point.recordedAt)
        if recorded > now + datetime.timedelta(minutes=5):
            continue  # clock-skewed future point
        rows.append(
            {
                "employee_id": current_user.id,
                "latitude": point.latitude,
                "longitude": point.longitude,
                "recorded_at": recorded,
                "is_moving": 1 if point.isMoving else 0,
                "accuracy": point.accuracy,
                "speed": point.speed,
                "battery_level": point.batteryLevel,
            }
        )
    if rows:
        if db.get_bind().dialect.name == "sqlite":
            db.execute(sqlite_insert(StaffLocation).prefix_with("OR IGNORE"), rows)
        else:
            db.execute(mysql_insert(StaffLocation).prefix_with("IGNORE"), rows)
        # Points are flowing again, so any earlier "location off" report is over.
        db.merge(StaffLocationState(employee_id=current_user.id, state="location_enabled", updated_at=now))
        db.commit()
    return {"saved": len(rows)}


@router.post("/state", status_code=204, summary="Report tracking on or off")
def report_state(
    body: StateIn,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """The phone reports that location tracking was switched on or off, so the map can tell 'location off' apart from 'no signal'."""
    if body.state not in REPORTABLE_STATES:
        raise HTTPException(status_code=422, detail=f"state must be one of {sorted(REPORTABLE_STATES)}")
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    db.merge(StaffLocationState(employee_id=current_user.id, state=body.state, updated_at=now))
    db.commit()


@router.get("/live", summary="Live staff locations")
def live_locations(db: Session = Depends(get_db), _: Employee = Depends(view_staff_locations)):
    """The newest known position and status of every staff member who has reported at least once. Admin and manager only."""
    latest = (
        db.query(StaffLocation.employee_id, func.max(StaffLocation.id).label("last_id"))
        .group_by(StaffLocation.employee_id)
        .subquery()
    )
    rows = (
        db.query(StaffLocation, Employee)
        .join(latest, StaffLocation.id == latest.c.last_id)
        .join(Employee, Employee.id == StaffLocation.employee_id)
        .filter(Employee.deleted_at.is_(None), Employee.is_active == 1)
        .all()
    )
    states = {s.employee_id: s for s in db.query(StaffLocationState).all()}
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    staff = []
    for point, employee in rows:
        reported = states.get(employee.id)
        if reported and reported.state == "signed_out" and reported.updated_at >= point.recorded_at:
            state = "signed_out"
        elif reported and reported.state == "location_disabled" and reported.updated_at >= point.recorded_at:
            state = "location_disabled"
        elif now - point.recorded_at > STALE_AFTER:
            state = "connection_lost"
        else:
            state = "moving" if point.is_moving else "stuck"
        staff.append(
            {
                "userId": employee.id,
                "name": employee.name,
                "phone": employee.phone,
                "latitude": float(point.latitude),
                "longitude": float(point.longitude),
                "address": point.address,
                "recordedAt": _iso(point.recorded_at),
                "state": state,
                "colorIndex": employee.id % COLOR_COUNT,
                "batteryLevel": point.battery_level,
                "speed": point.speed,
            }
        )
    staff.sort(key=lambda row: row["name"].lower())
    return {"staff": staff}


def _build_route(db: Session, employee_id: int, date: str) -> dict:
    try:
        day = datetime.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
    start_ist = datetime.datetime.combine(day, datetime.time.min, tzinfo=IST)
    start = start_ist.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    end = start + datetime.timedelta(days=1)
    rows = (
        db.query(StaffLocation)
        .filter(StaffLocation.employee_id == employee_id, StaffLocation.recorded_at >= start, StaffLocation.recorded_at < end)
        .order_by(StaffLocation.recorded_at)
        .all()
    )
    points = [
        {
            "latitude": float(r.latitude),
            "longitude": float(r.longitude),
            "address": r.address,
            "recordedAt": _iso(r.recorded_at),
            "state": "moving" if r.is_moving else "stuck",
        }
        for r in rows
    ]
    distance = sum(
        _haversine(points[i - 1]["latitude"], points[i - 1]["longitude"], points[i]["latitude"], points[i]["longitude"])
        for i in range(1, len(points))
    )

    # A stop is a run of consecutive points within the radius of its first point, spanning enough time.
    stops, i = [], 0
    while i < len(rows):
        last = i
        for j in range(i + 1, len(rows)):
            if _haversine(float(rows[i].latitude), float(rows[i].longitude), float(rows[j].latitude), float(rows[j].longitude)) > STOP_RADIUS_METERS:
                break
            last = j
        span = rows[last].recorded_at - rows[i].recorded_at
        if last > i and span >= STOP_MIN_DURATION:
            group = rows[i : last + 1]
            stops.append(
                {
                    "latitude": sum(float(r.latitude) for r in group) / len(group),
                    "longitude": sum(float(r.longitude) for r in group) / len(group),
                    "arrivedAt": _iso(rows[i].recorded_at),
                    "leftAt": _iso(rows[last].recorded_at),
                    "dwellMs": int(span.total_seconds() * 1000),
                    "address": rows[last].address or rows[i].address,
                }
            )
            i = last + 1
        else:
            i += 1

    return {
        "points": points,
        "stops": stops,
        "distanceMeters": round(distance, 1),
        "firstSeen": points[0]["recordedAt"] if points else None,
        "lastSeen": points[-1]["recordedAt"] if points else None,
    }


@router.get("/history", summary="Route for a staff member on a day")
def route_history(
    userId: int = Query(...),
    date: str = Query(..., description="YYYY-MM-DD, India time"),
    db: Session = Depends(get_db),
    _: Employee = Depends(view_staff_locations),
):
    """The route of one staff member (`userId`) for one date (`date`). Admin and manager only."""
    return _build_route(db, userId, date)


@router.get("/me", summary="My route for a day")
def my_route(
    date: str = Query(..., description="YYYY-MM-DD, India time"),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """The signed-in employee's own route for one date, for the 'My route' screen."""
    return _build_route(db, current_user.id, date)
