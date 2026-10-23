"""
Staff attendance — daily field check-in / check-out.

WHY THIS TABLE:
    Tracks when a sales executive starts and ends their field session each day.
    One row per employee per IST calendar date.
    A session is "open" until checkout_at is set.

COLUMNS:
    employee_id   — which employee checked in (cascade-deletes with the employee)
    date          — IST calendar date of the session
    checkin_at    — when they tapped "Start Check-in" (UTC stored)
    checkout_at   — when they tapped "Check out"; NULL while the session is open
    checkin_lat   — GPS latitude at check-in time (optional; may be NULL if permission was denied)
    checkin_lng   — GPS longitude at check-in time
"""
import datetime
from sqlalchemy import BigInteger, Date, DateTime, Float, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class StaffAttendance(Base):
    __tablename__ = "staff_attendance"
    __table_args__ = (
        # One session per employee per day — trying to check in twice returns 409.
        UniqueConstraint("employee_id", "date", name="uq_staff_attendance_employee_date"),
        Index("idx_staff_attendance_employee_date", "employee_id", "date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    checkin_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    checkout_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    checkin_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    checkin_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    employee: Mapped["Employee"] = relationship("Employee")
