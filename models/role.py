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
"""
import datetime
from typing import Optional

from sqlalchemy import Column, SmallInteger, DateTime, func
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
    created_at: datetime.datetime = Field(
        sa_column=Column("created_at", DateTime, nullable=False, server_default=func.now())
    )
    updated_at: datetime.datetime = Field(
        sa_column=Column(
            "updated_at", DateTime, nullable=False,
            server_default=func.now(), onupdate=func.now(),
        )
    )
