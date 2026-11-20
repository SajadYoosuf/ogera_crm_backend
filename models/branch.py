"""
Branch model — represents a physical office branch of a tenant.

WHAT IS THIS?
    Each tenant (ventino, etc.) has one or more branch offices.
    Branches are stored in this table so they can be managed by the admin
    without code changes — adding a new branch is just an INSERT, not a migration.

WHY NOT HARDCODE BRANCH NAMES?
    The old design used a PostgreSQL/MySQL ENUM or CHECK constraint with fixed names
    (malappuram, kottakkal, perinthalmanna). That means adding a new branch requires
    a database migration. By storing branches in a table, the admin can add branches
    from the UI during onboarding or later.

MULTI-TENANT:
    This table lives inside each tenant's PostgreSQL schema (e.g. ventino.branches).
    The same Python model class works for every tenant — the session's SET search_path
    determines which schema is queried. Ventino's branches are completely separate from
    any other tenant's branches.
"""

import datetime
from sqlalchemy import BigInteger, Boolean, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class Branch(Base):
    __tablename__ = "branches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Human-readable name shown in the UI (e.g. "Malappuram")
    name: Mapped[str] = mapped_column(String(100))
    # Short code used in filters and APIs (e.g. "malappuram")
    code: Mapped[str] = mapped_column(String(30), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
