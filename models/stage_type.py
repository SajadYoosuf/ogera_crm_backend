from sqlalchemy import String, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class StageType(Base):
    __tablename__ = "stage_types"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    description: Mapped[str | None] = mapped_column(String(255))
    order: Mapped[int] = mapped_column(SmallInteger)  # display order in UI
    is_active: Mapped[int] = mapped_column(SmallInteger, default=1)
