import datetime
from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class StaffLocationHistory(Base):
    __tablename__ = "staff_location_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
