import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class LeadActivity(Base):
    """A logged action or scheduled task on a contact: a call, visit, WhatsApp, quote, demo or note."""

    __tablename__ = "lead_activities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("contacts.id"))
    kind: Mapped[str] = mapped_column(String(20))
    subtype: Mapped[str | None] = mapped_column(String(30))
    notes: Mapped[str | None] = mapped_column(Text)
    # Set for scheduled tasks; a task is pending until marked done
    due_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(10), default="done")  # "pending" | "done"
    done_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    # GPS of a field visit
    latitude: Mapped[float | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[float | None] = mapped_column(Numeric(9, 6))
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    creator: Mapped["Employee"] = relationship("Employee", foreign_keys=[created_by])

    __table_args__ = (
        Index("idx_lead_activities_contact_id", "contact_id"),
        Index("idx_lead_activities_due", "status", "due_at"),
    )
