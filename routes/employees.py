from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from auth import get_current_user, get_password_hash, require_roles
from database import get_db
from mailer import send_employee_credentials, send_password_changed_notice
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


# Roles hidden from the employee list and detail for every viewer. Managers also hide other managers.
HIDDEN_FROM_LIST = {RoleEnum.admin.value}
HIDDEN_FROM_MANAGER = {RoleEnum.admin.value, RoleEnum.manager.value}


def _hidden_roles(viewer: Employee) -> set[str]:
    return HIDDEN_FROM_MANAGER if viewer.role == RoleEnum.manager.value else HIDDEN_FROM_LIST


def _get_employee_or_404(db: Session, employee_id: int, viewer: Employee | None = None) -> Employee:
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee or employee.deleted_at:
        raise HTTPException(status_code=404, detail="Employee not found")
    if viewer is not None and employee.role in _hidden_roles(viewer):
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


def _ensure_valid_team_lead(db: Session, team_lead_id: int | None, employee_id: int | None = None) -> None:
    """A team lead link must point to an active team_lead employee, never to the employee themselves."""
    if team_lead_id is None:
        return
    if employee_id is not None and team_lead_id == employee_id:
        raise HTTPException(status_code=400, detail="An employee cannot be their own team lead")
    lead = db.query(Employee).filter(Employee.id == team_lead_id, Employee.deleted_at.is_(None)).first()
    if lead is None or lead.role != RoleEnum.team_lead.value or not lead.is_active:
        raise HTTPException(status_code=422, detail="Team lead must be an active team_lead employee")


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


def _phone_key(phone: str) -> str:
    """Last 10 digits, so '+91 90000 00003' and '9000000003' are the same number."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    return digits[-10:]


def _phone_taken(db: Session, phone: str, exclude_id: int | None = None) -> bool:
    # Phone has no DB unique index, so soft-deleted employees don't reserve their number.
    # Compared in Python on the normalised key, so the country code and spacing don't matter.
    key = _phone_key(phone)
    q = db.query(Employee.id, Employee.phone).filter(Employee.phone.is_not(None), Employee.deleted_at.is_(None))
    if exclude_id is not None:
        q = q.filter(Employee.id != exclude_id)
    return any(_phone_key(stored) == key for _, stored in q.all() if key)


@router.get("/directory", summary="Staff directory")
def employee_directory(db: Session = Depends(get_db), _: Employee = Depends(get_current_user)):
    """Active staff with id, name, role and phone, for assignee pickers and the staff directory.
    Open to every signed-in employee. Deleted and deactivated staff are left out.
    """
    rows = (
        db.query(Employee)
        .filter(Employee.deleted_at.is_(None), Employee.is_active == 1)
        .order_by(func.lower(Employee.name))
        .all()
    )
    return [{"id": e.id, "name": e.name, "role": e.role, "phone": e.phone} for e in rows]


@router.get("", response_model=EmployeeListResponse, summary="List employees")
def list_employees(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    role: str | None = Query(None, description="Role slug, e.g. tele_caller"),
    is_active: bool | None = None,
    search: str | None = Query(None, description="Matches name, email or phone"),
    db: Session = Depends(get_db),
    viewer: Employee = Depends(get_current_user),
):
    """Paged list of employees with optional filters for role, active status and search (name, email or phone).
    Admin accounts are never returned. Managers also don't see other managers. Soft-deleted employees are excluded.
    Open to every signed-in employee.
    """
    q = db.query(Employee).filter(
        Employee.deleted_at.is_(None),
        Employee.role.notin_(_hidden_roles(viewer)),
    )
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


@router.get("/{employee_id}", response_model=EmployeeResponse, summary="Get an employee")
def get_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    viewer: Employee = Depends(get_current_user),
):
    """One employee by id. Returns 404 for an unknown id, a deleted employee, or a role hidden from the list."""
    return EmployeeResponse.model_validate(
        _get_employee_or_404(db, employee_id, viewer), from_attributes=True
    )


@router.post("", response_model=EmployeeCreateResponse, status_code=201, summary="Create an employee")
def create_employee(
    body: EmployeeCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(manage_employees),
):
    """Admin or manager creates an employee. Only an admin can assign the admin or manager role. The role must exist and be active.
    Email must be unique, ignoring case, and a deleted employee's email still counts. Phone must be unique by its last 10 digits, and a deleted employee's phone can be reused.
    The password is stored hashed and the login details are emailed. The outcome is in `credentials_email`.
    Returns 409 for a duplicate email or phone, and 422 for an unknown role.
    """
    _ensure_can_assign_role(actor, body.role)
    _ensure_role_assignable(db, body.role)
    _ensure_valid_team_lead(db, body.team_lead_id)
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


@router.put("/{employee_id}", response_model=EmployeeResponse, summary="Update an employee")
def update_employee(
    employee_id: int,
    body: EmployeeUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(manage_employees),
):
    """Admin or manager edits an employee. Only the fields sent are changed. Sending `password` resets it.
    Only an admin can change an admin or manager account, or grant those roles. Nobody can deactivate or change the role of their own account.
    Required fields cannot be null. Returns 409 for a duplicate email or phone.
    """
    employee = _get_employee_or_404(db, employee_id)
    data = body.model_dump(exclude_unset=True)

    # Null is not a valid value for these required columns.
    for field in ("name", "email", "role", "is_active", "password"):
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
    if "team_lead_id" in data:
        _ensure_valid_team_lead(db, data["team_lead_id"], employee_id=employee.id)
    # A team lead who stops being a team lead loses their team; the members are unlinked.
    if "role" in data and data["role"] != RoleEnum.team_lead.value and employee.role == RoleEnum.team_lead.value:
        db.query(Employee).filter(Employee.team_lead_id == employee.id).update({Employee.team_lead_id: None})

    if "email" in data and _email_taken(db, data["email"], exclude_id=employee.id):
        raise HTTPException(status_code=409, detail=DUPLICATE_EMAIL)
    if data.get("phone") and _phone_taken(db, data["phone"], exclude_id=employee.id):
        raise HTTPException(status_code=409, detail=DUPLICATE_PHONE)

    password_changed = "password" in data
    if password_changed:
        employee.password_hash = get_password_hash(data.pop("password"))
    for field, value in data.items():
        setattr(employee, field, value)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_EMAIL)
    db.refresh(employee)
    if password_changed:
        # Best effort: the password is already changed, so a failed email is only logged.
        send_password_changed_notice(employee.name, employee.email)
    return EmployeeResponse.model_validate(employee, from_attributes=True)


@router.delete("/{employee_id}", status_code=204, summary="Delete an employee")
def delete_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(manage_employees),
):
    """Soft delete. The row is kept for history and the employee is deactivated.
    You cannot delete your own account. Only an admin can delete an admin or manager.
    """
    employee = _get_employee_or_404(db, employee_id)
    if employee.id == actor.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    _ensure_can_modify(actor, employee, "delete")

    employee.deleted_at = datetime.utcnow()
    employee.is_active = 0
    db.query(Employee).filter(Employee.team_lead_id == employee.id).update({Employee.team_lead_id: None})
    db.commit()
