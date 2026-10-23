"""
Live GPS position for each active field employee.

WHY THIS TABLE:
    Stores the most-recent location ping from the mobile foreground tracking service.
    One row per employee (upserted on each ping). Older pings overwrite the same row,
    so the table never grows beyond one row per employee.
    The backend throttles writes: a ping is ignored when recorded_at is within 30 seconds.

COLUMNS:
    employee_id   — unique; one live position per employee
    latitude      — decimal degrees, WGS-84
    longitude     — decimal degrees, WGS-84
    recorded_at   — when this position was recorded (UTC); used for the 30-second throttle
"""
import datetime
from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class StaffLocation(Base):
    __tablename__ = "staff_locations"

    # One row per employee — the PK is on id for FK compatibility but employee_id is unique.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    employee: Mapped["Employee"] = relationship("Employee")
