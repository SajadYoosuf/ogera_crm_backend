import datetime
from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Numeric, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class StaffLocation(Base):
    """One GPS point reported by a staff member's phone."""

    __tablename__ = "staff_locations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="CASCADE"))
    latitude: Mapped[float] = mapped_column(Numeric(9, 6))
    longitude: Mapped[float] = mapped_column(Numeric(9, 6))
    # UTC. The phone's capture time, not the upload time.
    recorded_at: Mapped[datetime.datetime] = mapped_column(DateTime)
    is_moving: Mapped[int] = mapped_column(SmallInteger, default=1)
    accuracy: Mapped[float | None] = mapped_column(Float)
    speed: Mapped[float | None] = mapped_column(Float)
    battery_level: Mapped[int | None] = mapped_column(SmallInteger)
    address: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        # A retried upload must not create duplicate points.
        UniqueConstraint("employee_id", "recorded_at", name="uq_staff_locations_employee_time"),
        Index("idx_staff_locations_employee_time", "employee_id", "recorded_at"),
    )


class StaffLocationState(Base):
    """The newest tracking state a phone reported about itself (e.g. location switched off)."""

    __tablename__ = "staff_location_states"

    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True)
    state: Mapped[str] = mapped_column(String(30))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
