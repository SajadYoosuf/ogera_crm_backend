import datetime
from sqlalchemy import Index, SmallInteger, String, UniqueConstraint, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class EnumValue(Base):
    """One admin-editable dropdown option (activity type, loss reason, partner type, district).

    Lead sources and lead stages keep their own tables (`lead_sources`, `stage_types`) because contacts
    point at them; routes/settings.py presents all six categories through the same API.
    """

    __tablename__ = "enum_values"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(50))
    # Stored on the records that use it; never changes once created.
    value: Mapped[str] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[int] = mapped_column(SmallInteger, default=1)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("category", "value", name="uq_category_value"),
        Index("idx_category", "category"),
    )
