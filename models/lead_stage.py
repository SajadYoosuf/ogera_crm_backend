import datetime
from sqlalchemy import BigInteger, ForeignKey, DateTime, Enum as SAEnum, Text, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class LeadStage(Base):
    __tablename__ = "lead_stages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("contacts.id"))
    stage: Mapped[str] = mapped_column(
        SAEnum("new_lead", "first_call", "aware", "interested", "quoted", "won", "lost", "referral")
    )
    changed_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id"))
    changed_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    # Only relevant when stage = 'lost'
    loss_reason: Mapped[str | None] = mapped_column(
        SAEnum("price", "competitor", "timing", "not_interested", "other")
    )
    loss_notes: Mapped[str | None] = mapped_column(Text)

    # Only relevant when stage = 'aware'
    awareness_note: Mapped[str | None] = mapped_column(Text)

    contact: Mapped["Contact"] = relationship("Contact", back_populates="lead_stages")
    changed_by_employee: Mapped["Employee"] = relationship(
        "Employee", back_populates="lead_stage_changes"
    )

    __table_args__ = (
        Index("idx_lead_stages_contact_id", "contact_id"),
        Index("idx_lead_stages_stage", "stage"),
    )
