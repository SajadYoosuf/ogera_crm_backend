import datetime
from sqlalchemy import String, SmallInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class LeadSource(Base):
    __tablename__ = "lead_sources"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    is_active: Mapped[int] = mapped_column(SmallInteger, default=1)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    contacts: Mapped[list["Contact"]] = relationship("Contact", back_populates="lead_source")
