import datetime
from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Numeric, String, Text, Time, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class LeadActivity(Base):
    """An activity logged on a contact (call, visit, WhatsApp, quote, demo, note) or a scheduled follow-up task."""

    __tablename__ = "lead_activities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("contacts.id"))
    type: Mapped[str] = mapped_column(String(20))
    notes: Mapped[str | None] = mapped_column(Text)
    # A log entry is created "done"; a scheduled follow-up starts "pending" until completed
    status: Mapped[str] = mapped_column(String(10), default="done")
    # Only for scheduled follow-ups; the time is optional (date-only task)
    scheduled_date: Mapped[datetime.date | None] = mapped_column(Date)
    scheduled_time: Mapped[datetime.time | None] = mapped_column(Time)
    assigned_to: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id"))
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    completed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id"))
    # GPS of a field visit
    latitude: Mapped[float | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[float | None] = mapped_column(Numeric(9, 6))
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    creator: Mapped["Employee"] = relationship("Employee", foreign_keys=[created_by])
    assignee: Mapped["Employee | None"] = relationship("Employee", foreign_keys=[assigned_to])
    completer: Mapped["Employee | None"] = relationship("Employee", foreign_keys=[completed_by])

    __table_args__ = (
        Index("idx_lead_activities_contact_id", "contact_id"),
        Index("idx_lead_activities_due", "status", "scheduled_date"),
        Index("idx_lead_activities_assigned", "assigned_to"),
    )
