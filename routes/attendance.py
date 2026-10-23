"""
Attendance routes — daily field check-in / check-out.

WHAT THIS FILE DOES:
    POST /attendance/checkin   — start today's field session (GPS point recorded)
    POST /attendance/checkout  — end today's field session
    GET  /attendance/today     — return today's attendance record (or null if not checked in)

WHO CAN USE IT:
    Any active signed-in employee. Admin and manager may check in, but in practice
    only field staff use this from the mobile app.

BUSINESS RULES:
    - One session per employee per IST calendar day. A second check-in returns 409.
    - Checkout is only allowed when an open session exists for today.
    - GPS is optional: if the user denied location permission the coordinates are null.
"""
import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from auth import get_current_user
from business_time import ist_now, today_ist
from database import get_db
from models import Employee
from models.attendance import StaffAttendance

router = APIRouter(prefix="/attendance", tags=["attendance"], dependencies=[Depends(get_current_user)])


# ── Pydantic schemas ────────────────────────────────────────────────────────

class CheckinBody(BaseModel):
    latitude: float | None = None
    longitude: float | None = None


class AttendanceResponse(BaseModel):
    id: int
    date: datetime.date
    checkin_at: datetime.datetime
    checkout_at: datetime.datetime | None
    checkin_lat: float | None
    checkin_lng: float | None
    is_open: bool  # True when the session has no checkout_at yet

    class Config:
        from_attributes = True


# ── Helpers ─────────────────────────────────────────────────────────────────

def _to_response(record: StaffAttendance) -> AttendanceResponse:
    return AttendanceResponse(
        id=record.id,
        date=record.date,
        checkin_at=record.checkin_at,
        checkout_at=record.checkout_at,
        checkin_lat=record.checkin_lat,
        checkin_lng=record.checkin_lng,
        is_open=record.checkout_at is None,
    )


def _today_record(db: Session, employee_id: int) -> StaffAttendance | None:
    """Return today's attendance row for the employee, or None."""
    return (
        db.query(StaffAttendance)
        .filter(
            StaffAttendance.employee_id == employee_id,
            StaffAttendance.date == today_ist(),
        )
        .first()
    )


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get(
    "/today",
    response_model=AttendanceResponse | None,
    summary="Today's check-in record",
)
def get_today(
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Returns today's attendance record for the signed-in employee,
    or null (HTTP 200 with a null body) when they have not checked in yet.
    """
    record = _today_record(db, current_user.id)
    return _to_response(record) if record else None


@router.post(
    "/checkin",
    response_model=AttendanceResponse,
    status_code=201,
    summary="Start a field session",
)
def checkin(
    body: CheckinBody,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Records the start of today's field session.
    GPS coordinates are optional — send them when the app has location permission.
    Returns 409 when the employee has already checked in today.
    """
    existing = _today_record(db, current_user.id)
    if existing:
        raise HTTPException(status_code=409, detail="Already checked in today")

    record = StaffAttendance(
        employee_id=current_user.id,
        date=today_ist(),
        checkin_at=ist_now(),
        checkin_lat=body.latitude,
        checkin_lng=body.longitude,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return _to_response(record)


@router.post(
    "/checkout",
    response_model=AttendanceResponse,
    summary="End the current field session",
)
def checkout(
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Records the end of today's field session.
    Returns 404 when no check-in exists for today,
    and 409 when they have already checked out.
    """
    record = _today_record(db, current_user.id)
    if record is None:
        raise HTTPException(status_code=404, detail="No check-in found for today")
    if record.checkout_at is not None:
        raise HTTPException(status_code=409, detail="Already checked out today")

    record.checkout_at = ist_now()
    db.commit()
    return _to_response(record)
