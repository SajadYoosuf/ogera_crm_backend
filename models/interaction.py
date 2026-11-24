"""
Interaction model — maps to the existing `interactions` table in each tenant schema.

This table stores both activity logs (kind='log') and tasks (kind='task') for
any entity (leads, contacts, etc.). For the Leads MVP we only use entity_type='lead'.

COLUMN NOTES:
  entity_type   — 'lead' | 'contact' (which entity this row belongs to)
  entity_id     — the id of that entity
  kind          — 'log' (activity that happened) | 'task' (action to do)
  activity_type — call / field_visit / message / note / quotation / whatsapp
  custom_label  — overrides activity_type when kind='log' and label is custom
  title         — task title (kind='task' only)
  notes         — freeform text
  done_by       — FK → employees.id (who performed the activity)
  assigned_to   — FK → employees.id (who should do the task)
  created_by    — FK → employees.id (who created this row)
  due_at        — when the task is due (kind='task' only)
  priority      — high | medium | low | none
  status        — to_do | in_progress | completed (tasks)
  outcome       — interested / not_interested / no_answer / not_now (logs)
  parent_task_id — FK → self (for sub-tasks; not used by Leads MVP)
  logged_at     — when the activity happened (kind='log')
  created_at    — when this row was created
"""
import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Interaction(Base):
    __tablename__ = "interactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    entity_type: Mapped[str] = mapped_column(String(30))   # 'lead' | 'contact'
    entity_id: Mapped[int] = mapped_column(BigInteger)

    kind: Mapped[str] = mapped_column(String(10))          # 'log' | 'task'

    activity_type: Mapped[str | None] = mapped_column(String(50))
    custom_label: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)

    done_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="SET NULL"))
    assigned_to: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="SET NULL"))

    due_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[str | None] = mapped_column(String(20), default="none")
    status: Mapped[str | None] = mapped_column(String(20), default="todo")
    outcome: Mapped[str | None] = mapped_column(String(50))

    parent_task_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("interactions.id", ondelete="SET NULL"))

    logged_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_interactions_entity", "entity_type", "entity_id"),
        Index("idx_interactions_assigned", "assigned_to"),
        Index("idx_interactions_kind", "kind"),
        Index("idx_interactions_status", "status"),
    )
