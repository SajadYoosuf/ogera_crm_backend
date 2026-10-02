from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from auth import get_password_hash, require_roles
from database import get_db
from mailer import send_employee_credentials
from models import Employee, Role
from schemas import (
    EmployeeCreate,
    EmployeeCreateResponse,
    EmployeeListResponse,
    EmployeeResponse,
    EmployeeUpdate,
    RoleEnum,
)

router = APIRouter(prefix="/employees", tags=["employees"])

# Only these roles may use any employee endpoint.
manage_employees = require_roles(RoleEnum.admin, RoleEnum.manager)

# Roles that only an admin may grant, modify or delete.
ADMIN_ONLY_ROLES = {RoleEnum.admin.value, RoleEnum.manager.value}

DUPLICATE_EMAIL = "An employee with this email already exists"
DUPLICATE_PHONE = "An employee with this phone number already exists"


def _get_employee_or_404(db: Session, employee_id: int) -> Employee:
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee or employee.deleted_at:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


def _ensure_can_assign_role(actor: Employee, role: str) -> None:
    """Only admins may create or promote admins and managers."""
    if role in ADMIN_ONLY_ROLES and actor.role != RoleEnum.admin.value:
        raise HTTPException(status_code=403, detail=f"Only an admin can assign the {role} role")


def _ensure_role_assignable(db: Session, slug: str) -> None:
    """The role must exist (created via /roles) and be active."""
    role = db.query(Role).filter(Role.slug == slug).first()
    if role is None:
        raise HTTPException(status_code=422, detail=f"Role '{slug}' does not exist")
    if not role.is_active:
        raise HTTPException(status_code=422, detail=f"Role '{slug}' is inactive")


def _ensure_can_modify(actor: Employee, target: Employee, verb: str) -> None:
    """Only admins may modify/delete admins and managers (others' accounts, not their own)."""
    if (
        target.role in ADMIN_ONLY_ROLES
        and target.id != actor.id
        and actor.role != RoleEnum.admin.value
    ):
        raise HTTPException(status_code=403, detail=f"Only an admin can {verb} a {target.role}")


def _email_taken(db: Session, email: str, exclude_id: int | None = None) -> bool:
    # Soft-deleted rows still hold the unique email, so they are included here.
    q = db.query(Employee.id).filter(func.lower(Employee.email) == email.lower())
    if exclude_id is not None:
        q = q.filter(Employee.id != exclude_id)
    return q.first() is not None


def _phone_taken(db: Session, phone: str, exclude_id: int | None = None) -> bool:
    # Phone has no DB unique index, so soft-deleted employees don't reserve their number.
    q = db.query(Employee.id).filter(Employee.phone == phone, Employee.deleted_at.is_(None))
    if exclude_id is not None:
        q = q.filter(Employee.id != exclude_id)
    return q.first() is not None


@router.get("", response_model=EmployeeListResponse)
def list_employees(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    role: str | None = Query(None, description="Role slug, e.g. tele_caller"),
    is_active: bool | None = None,
    search: str | None = Query(None, description="Matches name, email or phone"),
    db: Session = Depends(get_db),
    _: Employee = Depends(manage_employees),
):
    """List employees (admin/manager only; soft-deleted are excluded)."""
    q = db.query(Employee).filter(Employee.deleted_at.is_(None))
    if role:
        q = q.filter(Employee.role == role)
    if is_active is not None:
        q = q.filter(Employee.is_active == int(is_active))
    if search:
        like = f"%{search.strip()}%"
        q = q.filter(
            or_(Employee.name.ilike(like), Employee.email.ilike(like), Employee.phone.ilike(like))
        )

    total = q.count()
    rows = (
        q.order_by(Employee.created_at.desc(), Employee.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return EmployeeListResponse(
        items=[EmployeeResponse.model_validate(e, from_attributes=True) for e in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{employee_id}", response_model=EmployeeResponse)
def get_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    _: Employee = Depends(manage_employees),
):
    """Get a single employee by ID (admin/manager only)."""
    return EmployeeResponse.model_validate(
        _get_employee_or_404(db, employee_id), from_attributes=True
    )


@router.post("", response_model=EmployeeCreateResponse, status_code=201)
def create_employee(
    body: EmployeeCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(manage_employees),
):
    """Create an employee (admin/manager only). The password is stored hashed and the
    login details are emailed to the employee; the outcome is reported in `credentials_email`."""
    _ensure_can_assign_role(actor, body.role)
    _ensure_role_assignable(db, body.role)
    if _email_taken(db, body.email):
        raise HTTPException(status_code=409, detail=DUPLICATE_EMAIL)
    if body.phone and _phone_taken(db, body.phone):
        raise HTTPException(status_code=409, detail=DUPLICATE_PHONE)

    data = body.model_dump(exclude={"password"})
    employee = Employee(**data, password_hash=get_password_hash(body.password))
    db.add(employee)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_EMAIL)
    db.refresh(employee)
    mail = send_employee_credentials(employee.name, employee.email, body.password, employee.role)
    return EmployeeCreateResponse(
        **EmployeeResponse.model_validate(employee, from_attributes=True).model_dump(),
        credentials_email=mail,
    )


@router.put("/{employee_id}", response_model=EmployeeResponse)
def update_employee(
    employee_id: int,
    body: EmployeeUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(manage_employees),
):
    """Update an employee (admin/manager only). Supplying `password` resets it."""
    employee = _get_employee_or_404(db, employee_id)
    data = body.model_dump(exclude_unset=True)

    # Null is not a valid value for these required columns.
    for field in ("name", "email", "role", "is_field_staff", "is_active", "password"):
        if field in data and data[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null")

    # Managers can't modify admins/managers, and only admins may grant those roles.
    _ensure_can_modify(actor, employee, "modify")
    if "role" in data and data["role"] != employee.role:
        _ensure_can_assign_role(actor, data["role"])
        _ensure_role_assignable(db, data["role"])
    if actor.id == employee.id and (
        data.get("is_active") is False or ("role" in data and data["role"] != employee.role)
    ):
        raise HTTPException(
            status_code=400, detail="You cannot deactivate or change the role of your own account"
        )

    if "email" in data and _email_taken(db, data["email"], exclude_id=employee.id):
        raise HTTPException(status_code=409, detail=DUPLICATE_EMAIL)
    if data.get("phone") and _phone_taken(db, data["phone"], exclude_id=employee.id):
        raise HTTPException(status_code=409, detail=DUPLICATE_PHONE)

    if "password" in data:
        employee.password_hash = get_password_hash(data.pop("password"))
    for field, value in data.items():
        setattr(employee, field, value)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_EMAIL)
    db.refresh(employee)
    return EmployeeResponse.model_validate(employee, from_attributes=True)


@router.delete("/{employee_id}", status_code=204)
def delete_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(manage_employees),
):
    """Soft delete an employee (admin/manager only)."""
    employee = _get_employee_or_404(db, employee_id)
    if employee.id == actor.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    _ensure_can_modify(actor, employee, "delete")

    employee.deleted_at = datetime.utcnow()
    employee.is_active = 0
    db.commit()
