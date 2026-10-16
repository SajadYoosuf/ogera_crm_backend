from sqlalchemy import String, SmallInteger, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class StageType(Base):
    __tablename__ = "stage_types"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(60))
    slug: Mapped[str] = mapped_column(String(40))
    contact_type: Mapped[str] = mapped_column(String(20))  # consumer | partner | dealer
    description: Mapped[str | None] = mapped_column(String(255))
    order: Mapped[int] = mapped_column(SmallInteger)
    is_active: Mapped[int] = mapped_column(SmallInteger, default=1)

    __table_args__ = (
        UniqueConstraint("slug", "contact_type", name="uq_stage_type_slug_per_type"),
    )
