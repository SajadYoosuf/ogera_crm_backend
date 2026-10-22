import datetime
from sqlalchemy import String, SmallInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, Session
from database import Base

# Built-in roles. They are seeded on startup, can't be deleted or deactivated, and the
# `admin` / `manager` slugs carry special authorization rules in routes/employees.py.
SYSTEM_ROLES = [
    ("admin", "Admin", "Full system access"),
    ("manager", "Manager", "Manages staff, all leads, reports"),
    ("sales_executive", "Sales Executive", "Sales executive (closes deals)"),
]


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Immutable machine name stored on employees.role (FK target).
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str | None] = mapped_column(String(255))
    is_system: Mapped[int] = mapped_column(SmallInteger, default=0)
    is_active: Mapped[int] = mapped_column(SmallInteger, default=1)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


def seed_system_roles(db: Session) -> None:
    """Insert any missing built-in roles (idempotent)."""
    existing = {slug for (slug,) in db.query(Role.slug).all()}
    for slug, name, description in SYSTEM_ROLES:
        if slug not in existing:
            db.add(Role(slug=slug, name=name, description=description, is_system=1))
    db.commit()
