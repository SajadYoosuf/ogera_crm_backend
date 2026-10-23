"""
followup_edit_log — audit trail for every field change on a follow-up or task.

Why: managers need to see if an employee changed the channel, priority, due date or assignee
on a task after it was created. One row per changed field, written by the PATCH handlers.

Columns:
  followup_id  — which follow-up was edited
  contact_id   — denormalised for fast filtering by contact (matches followup_schedule.contact_id)
  changed_by   — the employee who made the edit
  changed_at   — server timestamp of the change
  field_name   — which field changed: 'channel', 'priority', 'assigned_to', 'due_at',
                 'status', 'notes', 'title'
  old_value    — previous value as a string (None if field was previously empty)
  new_value    — new value as a string (None if field was cleared)
"""
import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class FollowupEditLog(Base):
    __tablename__ = "followup_edit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    followup_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("followup_schedule.id"))
    contact_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("contacts.id"), nullable=True)
    changed_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id"))
    changed_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    field_name: Mapped[str] = mapped_column(String(40))   # 'channel', 'priority', 'assigned_to', etc.
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    changer: Mapped["Employee"] = relationship("Employee", foreign_keys=[changed_by])

    __table_args__ = (
        Index("idx_fol_edit_followup", "followup_id"),
        Index("idx_fol_edit_contact", "contact_id"),
    )
