from sqlalchemy import String, SmallInteger, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class PipelineStage(Base):
    """One stage in the lead pipeline.

    Each row is one column on the Kanban board (e.g. "New Lead", "Interested", "Won").
    Every stage belongs to one contact type (consumer, partner or dealer) and has an
    order number that controls the left-to-right column order on the board.
    """
    __tablename__ = "pipeline_stages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(60))            # display label shown on the board
    slug: Mapped[str] = mapped_column(String(40))            # stable code-friendly key (e.g. "new_lead")
    contact_type: Mapped[str] = mapped_column(String(20))   # "consumer" | "partner" | "dealer"
    description: Mapped[str | None] = mapped_column(String(255))
    order: Mapped[int] = mapped_column(SmallInteger)         # column position (0 = leftmost)
    is_active: Mapped[int] = mapped_column(SmallInteger, default=1)

    __table_args__ = (
        UniqueConstraint("slug", "contact_type", name="uq_pipeline_stage_slug_per_type"),
    )
