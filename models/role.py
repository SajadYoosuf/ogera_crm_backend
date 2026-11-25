"""
Role model — SQLModel.

What is a Role?
  A role is a named permission level assigned to every employee.
  Examples: "admin", "manager", "sales_executive", or custom roles.

Multi-tenant note:
  No schema in __table_args__ — this table lives in whichever schema the connection's
  search_path is set to (e.g. ventino.roles). Same as employees.

Columns:
  slug      — machine-readable key (unique); used for permission checks in code.
               Set once at creation; never changes.
  name      — human-readable label (unique).
  is_active — 0 = hidden from the "assign role" dropdown; employees keep their role.
  parent_id, is_system, description, see_all_records, permissions — added by
            scripts/migrations/002_roles_permissions.sql.
"""
import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, JSON, SmallInteger, String, func
from sqlmodel import SQLModel, Field


class Role(SQLModel, table=True):
    __tablename__ = "roles"

    id: Optional[int] = Field(default=None, primary_key=True)

    # Machine-readable key used in permission checks. e.g. "admin", "manager".
    slug: str = Field(max_length=40, unique=True)

    name: str = Field(max_length=80, unique=True)

    is_active: int = Field(
        default=1,
        sa_column=Column("is_active", SmallInteger, nullable=False, server_default="1"),
    )

    # Columns added by scripts/migrations/002_roles_permissions.sql.
    parent_id: Optional[int] = Field(
        default=None,
        sa_column=Column("parent_id", ForeignKey("roles.id", ondelete="SET NULL"), nullable=True),
    )
    is_system: int = Field(
        default=0,
        sa_column=Column("is_system", SmallInteger, nullable=False, server_default="0"),
    )
    description: Optional[str] = Field(
        default=None,
        sa_column=Column("description", String(255), nullable=True),
    )
    # Heads (admin/manager) see every record; everyone else sees only what is assigned to them.
    see_all_records: bool = Field(
        default=False,
        sa_column=Column("see_all_records", Boolean, nullable=False, server_default="false"),
    )
    permissions: dict = Field(
        default_factory=dict,
        sa_column=Column("permissions", JSON, nullable=False, server_default="{}"),
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
