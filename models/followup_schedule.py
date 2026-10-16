import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class FollowupSchedule(Base):
    __tablename__ = "followup_schedule"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("contacts.id"))
    assigned_to: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id"))
    due_at: Mapped[datetime.datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending / done / skipped
    channel: Mapped[str] = mapped_column(String(15))  # call / visit / whatsapp
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    contact: Mapped["Contact"] = relationship("Contact", back_populates="followup_schedules")
    assignee: Mapped["Employee"] = relationship("Employee", foreign_keys=[assigned_to])

    __table_args__ = (
        Index("idx_followup_schedule_contact_id", "contact_id"),
        Index("idx_followup_schedule_assigned", "assigned_to", "status", "due_at"),
    )
