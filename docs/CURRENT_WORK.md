# Current Work — SQLModel Migration + Backend Verification

## What we are doing

Going through every model in the project **one at a time** and doing a full verification pass:

1. Check the **live DB columns** — does the model match what's actually in MySQL?
2. Migrate the **model** from `DeclarativeBase` to `SQLModel (table=True)`
3. Review the **schema** (Pydantic) — remove duplication, keep it simple
4. Review the **route** — fix imports, deprecated calls, access control
5. Run **tests** for that module — fix any wrong test expectations
6. Update **docs** — module doc + daily change log
7. **Commit** — one clean commit per model

## Why SQLModel

Old style (raw SQLAlchemy):
```python
class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
```

New style (SQLModel):
```python
class Employee(SQLModel, table=True):
    __tablename__ = "employees"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=120)
```

Both produce the same DB table. SQLModel is less boilerplate, works with Pydantic v2,
and gives IDE autocomplete on model fields.

## Rules agreed on

- **DB models** → `SQLModel, table=True` (one at a time, starting from simple models)
- **API schemas** → `Pydantic BaseModel` (request/response shapes, never `table=True`)
- `Base(DeclarativeBase)` stays in `database.py` for models not yet migrated
- `Base.registry = default_registry` — shares SQLModel's registry so relationships resolve across both styles
- `SessionLocal` kept as backward-compat export (scheduler, services, tests use it)
- `get_db()` uses `Session(engine, expire_on_commit=False)` from SQLModel

---

## Migration status

| Model file | Status | Notes |
| --- | --- | --- |
| `models/employee.py` | ✅ Done | Migrated + tests fixed + docs updated. Commit: `82b3e57` |
| `models/role.py` | ⏳ Next | Simple — no FK to other models, good starting point |
| `models/lead_source.py` | ⏳ Pending | Simple lookup table |
| `models/pipeline_stage.py` | ⏳ Pending | Referenced by lead_stage |
| `models/contact.py` | ⏳ Pending | Central model — many FKs and relationships |
| `models/lead_stage.py` | ⏳ Pending | Depends on contact + pipeline_stage |
| `models/fcm_device.py` | ⏳ Pending | |
| `models/followup_schedule.py` | ⏳ Pending | |
| `models/followup_edit_log.py` | ⏳ Pending | |
| `models/followup_reschedule.py` | ⏳ Pending | |
| `models/consumer_profile.py` | ⏳ Pending | |
| `models/partner_profile.py` | ⏳ Pending | |
| `models/dealer_profile.py` | ⏳ Pending | |
| `models/contact_assignment.py` | ⏳ Pending | |
| `models/contact_interested_product.py` | ⏳ Pending | |
| `models/sale.py` | ⏳ Pending | |
| `models/product.py` | ⏳ Pending | |
| `models/expense.py` | ⏳ Pending | |
| `models/attendance.py` | ⏳ Pending | |
| `models/staff_location.py` | ⏳ Pending | |
| `models/staff_location_history.py` | ⏳ Pending | |
| `models/notification.py` | ⏳ Pending | |
| `models/password_reset.py` | ⏳ Pending | |
| `models/activity_log.py` | ⏳ Pending | |
| `models/enum_value.py` | ⏳ Pending | |
| `models/company_setting.py` | ⏳ Pending | |

---

## Checklist for each model migration

Use the employee migration below as the reference pattern.

```
[ ] 1. Query live DB — confirm actual columns match the model
[ ] 2. Convert model to SQLModel (table=True) — use Field(), sa_column for complex types
[ ] 3. Check relationships — foreign_keys needed if two FKs point to the same table
[ ] 4. Check schema — remove duplication, keep validators in base class only
[ ] 5. Check route — fix Session import, datetime.utcnow, access control
[ ] 6. Run tests — fix any wrong expectations (not fixing correct behavior)
[ ] 7. Update docs/modules/<model>.md
[ ] 8. Write docs/changes/YYYY-MM-DD-<model>-sqlmodel.md
[ ] 9. Commit
```

---

## Completed example — Employee (2026-11-03)

### What we checked against live DB

```sql
SHOW COLUMNS FROM employees;
-- confirmed: id, name, email, password_hash, role, phone, branch,
--            fcm_token, is_active, created_at, updated_at, deleted_at
-- dropped:   team_lead_id (ALTER TABLE employees DROP COLUMN team_lead_id)
-- in DB but not in model (pending drop): is_field_staff
```

### Model before → after

**Before** (DeclarativeBase):
```python
class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(180), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(40), ForeignKey("roles.slug"), default="sales_executive")
    phone: Mapped[str | None] = mapped_column(String(20))
    branch: Mapped[str | None] = mapped_column(String(60))
    fcm_token: Mapped[str | None] = mapped_column(String(512))
    is_active: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
```

**After** (SQLModel):
```python
class Employee(SQLModel, table=True):
    __tablename__ = "employees"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=120)
    email: str = Field(max_length=180, unique=True)
    password_hash: str = Field(max_length=255)
    role: str = Field(default="sales_executive", max_length=40, foreign_key="roles.slug")
    phone: Optional[str] = Field(default=None, max_length=20)
    branch: Optional[str] = Field(default=None, max_length=60)
    fcm_token: Optional[str] = Field(default=None, max_length=512)
    is_active: int = Field(
        default=1,
        sa_column=Column("is_active", SmallInteger, nullable=False, server_default="1"),
    )
    created_at: datetime = Field(
        sa_column=Column("created_at", DateTime, nullable=False, server_default=func.now())
    )
    updated_at: datetime = Field(
        sa_column=Column("updated_at", DateTime, nullable=False,
                         server_default=func.now(), onupdate=func.now())
    )
    deleted_at: Optional[datetime] = Field(
        default=None, sa_column=Column("deleted_at", DateTime, nullable=True)
    )
    # Relationships
    assigned_contacts: List["Contact"] = Relationship(
        back_populates="assigned_employee",
        sa_relationship_kwargs={"foreign_keys": "[Contact.assigned_to]"},
    )
    created_contacts: List["Contact"] = Relationship(
        back_populates="creator",
        sa_relationship_kwargs={"foreign_keys": "[Contact.created_by]"},
    )
    lead_stage_changes: List["LeadStage"] = Relationship(back_populates="changed_by_employee")
```

### Key SQLModel patterns to remember

| Situation | How to write it |
| --- | --- |
| Simple column | `name: str = Field(max_length=120)` |
| Nullable column | `phone: Optional[str] = Field(default=None, max_length=20)` |
| Primary key | `id: Optional[int] = Field(default=None, primary_key=True)` |
| Foreign key | `role: str = Field(foreign_key="roles.slug")` |
| SmallInteger / server_default / onupdate | `sa_column=Column("col", SmallInteger, server_default="1")` |
| Relationship (simple) | `Relationship(back_populates="other_side")` |
| Relationship (two FKs to same table) | `Relationship(back_populates="...", sa_relationship_kwargs={"foreign_keys": "[OtherModel.col]"})` |

### Schema changes made

- `EmployeeUpdate` now extends `EmployeeBase` — validators inherited, not duplicated
- `CredentialsEmailStatus` class removed — `credentials_email: dict` is enough
- `_validate_password` returns `None` unchanged so it works for both Create and Update

### Route changes made

- `GET /employees` — changed `Depends(get_current_user)` to `Depends(manage_employees)` → sales executive gets 403
- `_get_employee_or_404` — added `viewer.id != employee.id` guard so self-lookup always works
- Fixed `from sqlalchemy.orm import Session` → `from sqlmodel import Session`
- Fixed `datetime.utcnow()` → `datetime.now(timezone.utc).replace(tzinfo=None)`

### Tests result

```
58 passed, 0 failed
```

---

## database.py — shared setup (do not change per model)

```python
from sqlmodel import SQLModel, Session, create_engine
from sqlmodel.main import default_registry
from sqlalchemy.orm import DeclarativeBase, sessionmaker

class Base(DeclarativeBase):
    registry = default_registry   # lets Base models and SQLModel models see each other

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

def get_db():
    with Session(engine, expire_on_commit=False) as db:
        yield db
```

---

## Branch

All work is on `refactoring_branch`. Merge to `main` after all models are done and full test suite passes.
