import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class FollowupReschedule(Base):
    """One move of a follow-up's due date. Kept so the lead's history can show that it was rescheduled."""

    __tablename__ = "followup_reschedule"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    followup_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("followup_schedule.id"), index=True)
    contact_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("contacts.id"), nullable=True)
    from_due_at: Mapped[datetime.datetime] = mapped_column(DateTime)
    to_due_at: Mapped[datetime.datetime] = mapped_column(DateTime)
    changed_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id"))
    changed_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    followup: Mapped["FollowupSchedule"] = relationship("FollowupSchedule")
    changer: Mapped["Employee"] = relationship("Employee", foreign_keys=[changed_by])
