import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("contacts.id"))
    done_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id"))
    activity_type: Mapped[str] = mapped_column(String(20))  # call / visit / whatsapp / note
    outcome: Mapped[str | None] = mapped_column(String(20))  # interested / not_now / no_answer / won / lost
    notes: Mapped[str | None] = mapped_column(Text)
    followup_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("followup_schedule.id"), nullable=True)
    # GPS for field visits
    latitude: Mapped[float | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[float | None] = mapped_column(Numeric(9, 6))
    logged_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    doer: Mapped["Employee"] = relationship("Employee", foreign_keys=[done_by])
    followup: Mapped["FollowupSchedule | None"] = relationship("FollowupSchedule")

    __table_args__ = (
        Index("idx_activity_log_contact_id", "contact_id"),
        Index("idx_activity_log_followup_id", "followup_id"),
    )
