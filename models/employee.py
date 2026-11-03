"""
Employee database model — SQLModel.

What is SQLModel here?
  `table=True` tells SQLModel: "this class maps to a real DB table called `employees`".
  Each attribute with `Field()` becomes a column in that table.
  `Relationship()` is a Python-only link — it doesn't create a column, it lets you do
  things like `employee.assigned_contacts` to get all contacts assigned to this employee.

Why SQLModel instead of plain SQLAlchemy?
  SQLModel combines SQLAlchemy (DB queries) and Pydantic (validation) in one class.
  You get type checking, IDE autocomplete, and DB mapping all from one definition.

How a request flows through this model:
  1. A route calls `db.query(Employee).filter(...)` → SQLModel runs SQL → returns Employee objects.
  2. The route reads fields like `employee.name` or `employee.is_active`.
  3. To update: change a field, call `db.commit()` → saved to MySQL.

For columns that need special MySQL types (SmallInteger, server_default, onupdate),
we pass `sa_column=Column(...)` inside Field(). This gives full SQLAlchemy control
while keeping the SQLModel style for everything else.
"""
import datetime
from typing import Optional, List, TYPE_CHECKING

from sqlalchemy import Column, SmallInteger, DateTime, String, func
from sqlmodel import SQLModel, Field, Relationship

if TYPE_CHECKING:
    # TYPE_CHECKING is False at runtime — these imports only help the IDE understand types.
    # Avoids circular imports between employee ↔ contact ↔ lead_stage.
    from .contact import Contact
    from .lead_stage import LeadStage


class Employee(SQLModel, table=True):
    """One row = one employee account."""

    __tablename__ = "employees"

    # --- Columns ---

    # Primary key. Optional[int] because MySQL generates the value on INSERT — Python
    # doesn't know the id until after the row is saved.
    id: Optional[int] = Field(default=None, primary_key=True)

    name: str = Field(max_length=120)

    # unique=True → MySQL enforces one account per email address.
    email: str = Field(max_length=180, unique=True)

    # Never expose this in API responses. Always stored hashed (argon2).
    password_hash: str = Field(max_length=255)

    # Slug matching a row in `roles` (e.g. "sales_executive", "manager", "admin").
    # foreign_key enforces referential integrity at the DB level.
    role: str = Field(
        default="sales_executive",
        max_length=40,
        foreign_key="roles.slug",
    )

    # unique=True → MySQL enforces one account per phone number (NULLs are exempt).
    # Phones are stored as last 10 digits (normalized in the Pydantic schema) so
    # "+919000000003" and "9000000003" both land as "9000000003" and collide correctly.
    phone: Optional[str] = Field(
        default=None,
        sa_column=Column("phone", String(20), nullable=True, unique=True),
    )

    # One of: malappuram, perinthalmanna, kottakkal. Nullable for older rows.
    branch: Optional[str] = Field(default=None, max_length=60)

    # Legacy push token — still written as a fallback while old app versions are active.
    # New device registrations go to the fcm_devices table instead.
    fcm_token: Optional[str] = Field(default=None, max_length=512)

    # 1 = active employee, 0 = deactivated.
    # sa_column used here because Field() alone doesn't support SmallInteger.
    is_active: int = Field(
        default=1,
        sa_column=Column("is_active", SmallInteger, nullable=False, server_default="1"),
    )

    # MySQL sets these automatically — never assign them manually in Python.
    # server_default: MySQL writes CURRENT_TIMESTAMP when the row is first inserted.
    # onupdate: MySQL updates the value every time the row is changed.
    created_at: datetime.datetime = Field(
        sa_column=Column("created_at", DateTime, nullable=False, server_default=func.now())
    )
    updated_at: datetime.datetime = Field(
        sa_column=Column(
            "updated_at", DateTime, nullable=False,
            server_default=func.now(), onupdate=func.now()
        )
    )

    # Soft delete: NULL = employee is active. Non-NULL = deleted timestamp.
    # Always filter active employees with: WHERE deleted_at IS NULL
    deleted_at: Optional[datetime.datetime] = Field(
        default=None,
        sa_column=Column("deleted_at", DateTime, nullable=True),
    )

    # --- Relationships (Python only — no extra columns) ---

    # All contacts currently assigned to this employee.
    # foreign_keys needed because Contact has two FK columns pointing to employees
    # (assigned_to and created_by) — we must tell SQLAlchemy which one to use here.
    assigned_contacts: List["Contact"] = Relationship(
        back_populates="assigned_employee",
        sa_relationship_kwargs={"foreign_keys": "[Contact.assigned_to]"},
    )

    # All contacts this employee originally created (created_by column on contacts).
    # Different from `contacts` above — an employee can create a contact and then
    # it gets reassigned to someone else. Both FKs point to employees, so foreign_keys
    # is required here too.
    created_contacts: List["Contact"] = Relationship(
        back_populates="creator",
        sa_relationship_kwargs={"foreign_keys": "[Contact.created_by]"},
    )

    # All lead stage changes (e.g. moved to Won, Lost) recorded by this employee.
    lead_stage_changes: List["LeadStage"] = Relationship(
        back_populates="changed_by_employee",
    )
