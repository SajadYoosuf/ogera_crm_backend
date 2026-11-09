from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session
from auth import get_current_user, get_password_hash, require_admin_or_manager
from database import get_db
from mailer import send_employee_credentials, send_password_changed_notice
from models import Employee, FcmDevice, Role
from request_context import client_platform
from schemas import (
    EmployeeCreate,
    EmployeeCreateResponse,
    EmployeeListResponse,
    EmployeeResponse,
    EmployeeUpdate,
)

router = APIRouter(prefix="/employees", tags=["employees"])

# Adding, editing and deleting employees is for admin and manager (the one guard the backend keeps).
manage_employees = require_admin_or_manager

DUPLICATE_EMAIL = "An employee with this email already exists"
DUPLICATE_PHONE = "An employee with this phone number already exists"


def _hidden_roles(viewer: Employee) -> frozenset[str]:
    """Role slugs invisible to this viewer in list and by-id responses.

    Admin accounts are excluded from the employee list for every viewer — they are
    managed separately and should not appear in day-to-day staff management.
    A manager viewer additionally cannot see other manager-role employees.
    """
    if viewer.role == "manager":
        return frozenset({"admin", "manager"})
    return frozenset({"admin"})


def _get_employee_or_404(db: Session, employee_id: int, viewer: Employee | None = None) -> Employee:
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee or employee.deleted_at:
        raise HTTPException(status_code=404, detail="Employee not found")
    # Hidden-role accounts (e.g. admin for all viewers, manager for manager viewers) always
    # return 404 — even when the viewer is fetching their own record.
    if viewer and employee.role in _hidden_roles(viewer):
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


def _ensure_role_assignable(db: Session, slug: str, actor: Employee | None = None) -> None:
    """The role must exist (created via /roles) and be active.
    A manager cannot assign the admin or manager role — only an admin may do that.
    """
    role = db.query(Role).filter(Role.slug == slug).first()
    if role is None:
        raise HTTPException(status_code=422, detail=f"Role '{slug}' does not exist")
    if not role.is_active:
        raise HTTPException(status_code=422, detail=f"Role '{slug}' is inactive")
    if actor and actor.role == "manager" and slug in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Managers cannot assign the admin or manager role")


def _email_taken(db: Session, email: str, exclude_id: int | None = None) -> bool:
    # Soft-deleted rows still hold the unique email, so they are included here.
    q = db.query(Employee.id).filter(func.lower(Employee.email) == email.lower())
    if exclude_id is not None:
        q = q.filter(Employee.id != exclude_id)
    return q.first() is not None



def _phone_taken(db: Session, phone: str, exclude_id: int | None = None) -> bool:
    # Phones are stored as last 10 digits (normalized by the schema validator), so a
    # direct equality check is enough — no Python iteration over all rows needed.
    # Soft-deleted rows are excluded: a deleted employee's number can be reused.
    q = db.query(Employee.id).filter(
        Employee.phone == phone,
        Employee.deleted_at.is_(None),
    )
    if exclude_id is not None:
        q = q.filter(Employee.id != exclude_id)
    return q.first() is not None


@router.get("/directory", summary="Staff directory")
def employee_directory(db: Session = Depends(get_db), viewer: Employee = Depends(get_current_user)):
    """Active staff with id, name, role and phone, for assignee pickers and the staff directory.
    Open to every signed-in employee. Deleted, deactivated, and hidden-role staff are left out.
    Admin accounts are hidden from all viewers; manager accounts are hidden from manager viewers.
    """
    hidden = _hidden_roles(viewer)
    rows = (
        db.query(Employee)
        .filter(
            Employee.deleted_at.is_(None),
            Employee.is_active == 1,
            Employee.role.not_in(list(hidden)),
        )
        .order_by(func.lower(Employee.name))
        .all()
    )
    return [{"id": e.id, "name": e.name, "role": e.role, "phone": e.phone} for e in rows]


@router.get("", response_model=EmployeeListResponse, summary="List employees")
def list_employees(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    role: str | None = Query(None, description="Role slug, e.g. sales_executive"),
    is_active: bool | None = None,
    search: str | None = Query(None, description="Matches name, email or phone"),
    db: Session = Depends(get_db),
    viewer: Employee = Depends(get_current_user),
    platform: str = Depends(client_platform),
):
    """Paged list of employees with optional filters for role, active status and search (name, email or phone).
    Admin and manager can list from any client. Sales executives and other roles may only list from the
    mobile app (x-client-type: mobile). Role-based visibility hides admin accounts from all viewers
    and manager accounts from manager viewers.
    """
    if viewer.role not in ("admin", "manager") and platform != "mobile":
        raise HTTPException(status_code=403, detail="You do not have permission to perform this action")
    hidden = _hidden_roles(viewer)
    q = db.query(Employee).filter(
        Employee.deleted_at.is_(None),
        Employee.role.not_in(list(hidden)),
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
    """One employee by id. Hidden-role accounts (admin for all; manager for manager viewers) return
    404 before any access check. Sales executives may only fetch their own record (403 otherwise).
    Admin and manager may fetch any visible record.
    """
    employee = _get_employee_or_404(db, employee_id, viewer=viewer)
    # After confirming the employee exists and is visible, check that the viewer is allowed to
    # read someone else's full profile. Sales executives can only look up themselves.
    if viewer.id != employee_id and viewer.role not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="You do not have permission to view this employee")
    return EmployeeResponse.model_validate(employee, from_attributes=True)


@router.post("", response_model=EmployeeCreateResponse, status_code=201, summary="Create an employee")
def create_employee(
    body: EmployeeCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    actor: Employee = Depends(manage_employees),
):
    """Admin or manager creates an employee. Only an admin can assign the admin or manager role. The role must exist and be active.
    Email must be unique, ignoring case, and a deleted employee's email still counts. Phone must be unique by its last 10 digits, and a deleted employee's phone can be reused.
    The employee is committed before the credentials email is queued, so SMTP
    latency never holds an open database transaction.
    Returns 409 for a duplicate email or phone, and 422 for an unknown role.
    """
    _ensure_role_assignable(db, body.role, actor=actor)
    if _email_taken(db, body.email):
        raise HTTPException(status_code=409, detail=DUPLICATE_EMAIL)
    if body.phone and _phone_taken(db, body.phone):
        raise HTTPException(status_code=409, detail=DUPLICATE_PHONE)

    data = body.model_dump(exclude={"password"})
    employee = Employee(**data, password_hash=get_password_hash(body.password))
    db.add(employee)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Both email and phone have DB unique indexes — inspect which one fired.
        detail = DUPLICATE_PHONE if "phone" in str(exc.orig).lower() else DUPLICATE_EMAIL
        raise HTTPException(status_code=409, detail=detail)

    db.refresh(employee)

    background_tasks.add_task(
        send_employee_credentials,
        employee.name,
        employee.email,
        body.password,
        employee.role,
    )

    return EmployeeCreateResponse(
        **EmployeeResponse.model_validate(employee, from_attributes=True).model_dump(),
        credentials_email={
            "sent": False,
            "message": "Credentials email queued for delivery",
        },
    )


@router.put("/{employee_id}", response_model=EmployeeResponse, summary="Update an employee")
def update_employee(
    employee_id: int,
    body: EmployeeUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(manage_employees),
):
    """Admin or manager edits an employee. Only the fields sent are changed. Sending `password` resets it.
    Nobody can deactivate or change the role of their own account.
    Required fields cannot be null. Returns 409 for a duplicate email or phone.
    """
    employee = _get_employee_or_404(db, employee_id, viewer=actor)
    data = body.model_dump(exclude_unset=True)

    # Null is not a valid value for these required columns.
    for field in ("name", "email", "role", "is_active", "password"):
        if field in data and data[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null")

    if "role" in data and data["role"] != employee.role:
        _ensure_role_assignable(db, data["role"], actor=actor)
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

    password_changed = "password" in data
    if password_changed:
        employee.password_hash = get_password_hash(data.pop("password"))
    for field, value in data.items():
        setattr(employee, field, value)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        detail = DUPLICATE_PHONE if "phone" in str(exc.orig).lower() else DUPLICATE_EMAIL
        raise HTTPException(status_code=409, detail=detail)
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
    You cannot delete your own account.
    """
    employee = _get_employee_or_404(db, employee_id, viewer=actor)
    if employee.id == actor.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")

    employee.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    employee.is_active = 0
    employee.fcm_token = None
    # Free the phone so another employee can be assigned the same number.
    # The DB unique index on phone allows multiple NULLs, so clearing it here
    # is the correct way to release the slot for a soft-deleted row.
    employee.phone = None
    # Remove all push-notification device registrations so the deleted account
    # stops receiving notifications. The FK has ON DELETE CASCADE for hard deletes,
    # but soft deletes don't fire the cascade, so we clear them manually.
    db.query(FcmDevice).filter(FcmDevice.employee_id == employee.id).delete(synchronize_session=False)
    db.commit()


class _FcmTokenBody(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    platform: str | None = Field(default=None, max_length=20)
    device_id: str | None = Field(default=None, max_length=255)


@router.post("/me/fcm-token", summary="Register or update FCM device token")
def register_fcm_token(
    body: _FcmTokenBody,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    POST /employees/me/fcm-token — store the caller's FCM push token.

    Called by the web app and mobile app after login. The token is used by the backend to
    send push notifications to this employee's browsers and phones.
    The same token is updated idempotently when the client starts again.
    """
    token = body.token.strip()
    existing = db.query(FcmDevice).filter(FcmDevice.token == token).first()
    if existing:
        existing.employee_id = current_user.id
        existing.platform = body.platform
        existing.device_id = body.device_id
        existing.last_seen_at = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        db.add(FcmDevice(
            employee_id=current_user.id,
            token=token,
            platform=body.platform,
            device_id=body.device_id,
        ))
    # Keep the legacy field populated while older workers are being upgraded.
    current_user.fcm_token = token
    db.commit()
    return {"ok": True}


@router.delete("/me/fcm-token", summary="Unregister an FCM device token")
def unregister_fcm_token(
    body: _FcmTokenBody,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Remove one browser/phone registration, normally called on logout."""
    token = body.token.strip()
    db.query(FcmDevice).filter(
        FcmDevice.employee_id == current_user.id,
        FcmDevice.token == token,
    ).delete(synchronize_session=False)
    if current_user.fcm_token == token:
        current_user.fcm_token = None
    db.commit()
    return {"ok": True}
