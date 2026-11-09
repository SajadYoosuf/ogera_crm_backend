"""
Role model — SQLModel.

What is a Role?
  A role is a named permission level assigned to every employee.
  Examples: "admin", "manager", "sales_executive", or custom roles.

Why SQLModel?
  Same pattern as Employee — `table=True` maps the class to the `roles` table.
  `Field()` replaces `mapped_column()`. Special MySQL types (SmallInteger,
  server_default) are passed via `sa_column=Column(...)`.

Columns:
  slug        — machine-readable key (unique); stored on employees.role as FK target.
                Set once at creation; never changes.
  name        — human-readable label (unique).
  is_system   — 1 for built-in roles (admin, manager, sales_executive).
                System roles cannot be deactivated or deleted.
  is_active   — 0 = hidden from the "assign role" dropdown; employees keep their role.
"""
import datetime
from typing import Optional

from sqlalchemy import Column, SmallInteger, DateTime, func
from sqlmodel import SQLModel, Field, Session

# Built-in roles. Seeded on startup. `admin` / `manager` slugs carry special
# authorization rules in routes/employees.py and services/lead_access.py.
SYSTEM_ROLES = [
    ("admin", "Admin", "Full system access"),
    ("manager", "Manager", "Manages staff, all leads, reports"),
    ("sales_executive", "Sales Executive", "Sales executive (closes deals)"),
]


class Role(SQLModel, table=True):
    __tablename__ = "roles"

    id: Optional[int] = Field(default=None, primary_key=True)
    # Immutable machine name stored on employees.role (FK target).
    slug: str = Field(max_length=40, unique=True)
    name: str = Field(max_length=80, unique=True)
    description: Optional[str] = Field(default=None, max_length=255)
    is_system: int = Field(
        default=0,
        sa_column=Column("is_system", SmallInteger, nullable=False, server_default="0"),
    )
    is_active: int = Field(
        default=1,
        sa_column=Column("is_active", SmallInteger, nullable=False, server_default="1"),
    )
    created_at: datetime.datetime = Field(
        sa_column=Column("created_at", DateTime, nullable=False, server_default=func.now())
    )
    updated_at: datetime.datetime = Field(
        sa_column=Column(
            "updated_at", DateTime, nullable=False,
            server_default=func.now(), onupdate=func.now(),
        )
    )


def seed_system_roles(db: Session) -> None:
    """Insert any missing built-in roles (idempotent — safe to call on every startup)."""
    existing = {slug for (slug,) in db.query(Role.slug).all()}
    for slug, name, description in SYSTEM_ROLES:
        if slug not in existing:
            db.add(Role(slug=slug, name=name, description=description, is_system=1))
    db.commit()
