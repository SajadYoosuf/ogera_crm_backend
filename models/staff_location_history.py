import datetime
from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class StaffLocationHistory(Base):
    __tablename__ = "staff_location_history"
    # Composite index covers the common query: WHERE employee_id = ? AND recorded_at BETWEEN ? AND ?
    # MySQL can use the leftmost prefix for employee_id-only lookups too.
    __table_args__ = (
        Index("idx_staff_location_history_employee_recorded", "employee_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
