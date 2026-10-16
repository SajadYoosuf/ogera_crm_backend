import datetime
from sqlalchemy import BigInteger, Integer, ForeignKey, DateTime, String, Text, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class LeadStage(Base):
    __tablename__ = "lead_stage_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("contacts.id"))
    stage_id: Mapped[int] = mapped_column(Integer, ForeignKey("stage_types.id"))
    changed_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id"))
    changed_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    # Only relevant when the stage slug is 'lost'
    loss_reason: Mapped[str | None] = mapped_column(String(40))
    loss_notes: Mapped[str | None] = mapped_column(Text)

    # Only relevant when the stage slug is 'aware'
    awareness_note: Mapped[str | None] = mapped_column(Text)

    contact: Mapped["Contact"] = relationship("Contact", back_populates="lead_stages")
    stage_type: Mapped["StageType"] = relationship("StageType")
    changed_by_employee: Mapped["Employee"] = relationship(
        "Employee", back_populates="lead_stage_changes"
    )

    __table_args__ = (
        Index("idx_lead_stage_log_contact_id", "contact_id"),
        Index("idx_lead_stage_log_stage_id", "stage_id"),
    )
