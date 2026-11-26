"""
Employee database model — SQLModel.

Multi-tenant note:
  This model has NO schema in __table_args__. The table resolves to whichever schema
  the connection's search_path is set to (e.g. SET search_path TO ventino).
  database.get_session_for_schema() sets that before every query.

How a request flows through this model:
  1. A route calls db.scalar(select(Employee).where(...)) → SQLAlchemy runs SQL →
     returns an Employee object.
  2. The route reads fields like employee.name or employee.is_active.
  3. To update: change a field, call db.commit() → saved to PostgreSQL.

role_id is an integer FK to roles.id. At login, get_current_user() also loads the
Role row and attaches employee._role_slug as a transient attribute for permission checks.
"""
import datetime
from typing import Optional, List, TYPE_CHECKING

from sqlalchemy import BigInteger, Column, ForeignKey, SmallInteger, DateTime, String, func
from sqlmodel import SQLModel, Field, Relationship

if TYPE_CHECKING:
    from .contact import Contact
    from .lead_stage import LeadStage


class Employee(SQLModel, table=True):
    """One row = one employee account."""

    __tablename__ = "employees"

    id: Optional[int] = Field(default=None, primary_key=True)

    name: str = Field(max_length=120)

    email: str = Field(max_length=180, unique=True)

    # Never expose this in API responses. Always stored hashed (argon2).
    password_hash: str = Field(max_length=255)

    # Integer FK to roles.id. Use employee._role_slug (set by get_current_user)
    # for permission checks — it holds the role's slug string.
    role_id: int = Field(foreign_key="roles.id")

    phone: Optional[str] = Field(
        default=None,
        sa_column=Column("phone", String(20), nullable=True, unique=True),
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

    # Soft delete: NULL = employee is active. Non-NULL = deleted timestamp.
    deleted_at: Optional[datetime.datetime] = Field(
        default=None,
        sa_column=Column("deleted_at", DateTime, nullable=True),
    )

    # Latest FCM push token (web or mobile). Notification dispatch also reads fcm_devices.
    fcm_token: Optional[str] = Field(
        default=None,
        sa_column=Column("fcm_token", String(512), nullable=True),
    )

    # FK to branches table (ventino schema). Tells which branch office this employee belongs to.
    # Used in contacts list to show "which branch created this lead".
    branch_id: Optional[int] = Field(
        default=None,
        sa_column=Column("branch_id", BigInteger, ForeignKey("branches.id"), nullable=True),
    )

    # --- Relationships (Python only — no extra columns) ---

    assigned_contacts: List["Contact"] = Relationship(
        back_populates="assigned_employee",
        sa_relationship_kwargs={"foreign_keys": "[Contact.assigned_to]"},
    )

    created_contacts: List["Contact"] = Relationship(
        back_populates="creator",
        sa_relationship_kwargs={"foreign_keys": "[Contact.created_by]"},
    )

    lead_stage_changes: List["LeadStage"] = Relationship(
        back_populates="changed_by_employee",
    )

    # branch_id FK is defined above. The Branch relationship is NOT declared here
    # because Branch uses SQLAlchemy Base (not SQLModel) and SQLModel's metaclass
    # cannot handle the type annotation correctly. If branch details are needed for
    # an employee, query Branch directly by branch_id.
