import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class RecordLog(Base):
    """One row per change to a lead or contact: created, edited, status_changed, assigned, and so on.

    Shared by leads and contacts. Activities and follow-ups live in `interactions`, not here;
    changes to them are logged against the parent lead (e.g. action 'task_completed').
    """

    __tablename__ = "record_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(20))  # 'lead' or 'contact'
    entity_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id"), nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_record_logs_entity", "entity_type", "entity_id", "created_at"),
    )
