from datetime import datetime, timezone
import logging
from fastapi import APIRouter, Depends, HTTPException, Query

logger = logging.getLogger(__name__)
from pydantic import BaseModel, Field, field_validator
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


# ── Role helpers ──────────────────────────────────────────────────────────────

def _viewer_slug(viewer: Employee) -> str | None:
    """
    Return the role slug for the logged-in employee.

    WHY NOT employee.role?
      The Employee model stores role_id (int FK to roles.id), not a role string.
      get_current_user() attaches the slug as the transient attribute _role_slug
      so we don't have to hit the DB on every request.
    """
    return getattr(viewer, "_role_slug", None)


def _load_slug(emp: Employee, db: Session) -> str | None:
    """
    Return the role slug for an employee fetched directly from the DB
    (i.e. NOT through get_current_user, so _role_slug is not set yet).
    """
    slug = getattr(emp, "_role_slug", None)
    if slug:
        return slug
    role = db.query(Role).filter(Role.id == emp.role_id).first()
    return role.slug if role else None


def _role_ids_for_slugs(db: Session, slugs: frozenset[str]) -> list[int]:
    """Look up role IDs for a set of slug strings — used to build SQL filters."""
    if not slugs:
        return []
    rows = db.query(Role.id).filter(Role.slug.in_(list(slugs))).all()
    return [r.id for r in rows]


def _hidden_role_ids(db: Session, viewer: Employee) -> list[int]:
    """
    Role IDs that this viewer should NOT see in list / detail responses.

    Admin IDs are hidden from everyone. Manager IDs are hidden from managers.
    Uses _viewer_slug(viewer) to decide the viewer's level.
    """
    slug = _viewer_slug(viewer)
    if slug == "manager":
        slugs = frozenset({"admin", "manager"})
    else:
        slugs = frozenset({"admin"})
    return _role_ids_for_slugs(db, slugs)


def _get_employee_or_404(db: Session, employee_id: int, viewer: Employee | None = None) -> Employee:
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee or employee.deleted_at:
        raise HTTPException(status_code=404, detail="Employee not found")
    if viewer:
        hidden_ids = _hidden_role_ids(db, viewer)
        if employee.role_id in hidden_ids:
            raise HTTPException(status_code=404, detail="Employee not found")
    return employee


def _ensure_role_assignable(db: Session, slug: str, actor: Employee | None = None) -> Role:
    """
    The role must exist and be active. Returns the Role row.
    A manager cannot assign admin or manager — only an admin may do that.
    """
    role = db.query(Role).filter(Role.slug == slug).first()
    if role is None:
        raise HTTPException(status_code=422, detail=f"Role '{slug}' does not exist")
    if not role.is_active:
        raise HTTPException(status_code=422, detail=f"Role '{slug}' is inactive")
    if actor and _viewer_slug(actor) == "manager" and slug in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Managers cannot assign the admin or manager role")
    return role


def _email_taken(db: Session, email: str, exclude_id: int | None = None) -> bool:
    q = db.query(Employee.id).filter(func.lower(Employee.email) == email.lower())
    if exclude_id is not None:
        q = q.filter(Employee.id != exclude_id)
    return q.first() is not None


def _phone_taken(db: Session, phone: str, exclude_id: int | None = None) -> bool:
    q = db.query(Employee.id).filter(
        Employee.phone == phone,
        Employee.deleted_at.is_(None),
    )
    if exclude_id is not None:
        q = q.filter(Employee.id != exclude_id)
    return q.first() is not None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/directory", summary="Staff directory")
def employee_directory(db: Session = Depends(get_db), viewer: Employee = Depends(get_current_user)):
    """Active staff with id, name, role slug and phone, for assignee pickers and the staff directory.
    Admin accounts are hidden from all viewers; manager accounts are hidden from manager viewers.
    """
    hidden_ids = _hidden_role_ids(db, viewer)
    rows = (
        db.query(Employee, Role.slug.label("role_slug"))
        .join(Role, Employee.role_id == Role.id)
        .filter(
            Employee.deleted_at.is_(None),
            Employee.is_active == 1,
            Employee.role_id.not_in(hidden_ids) if hidden_ids else True,
        )
        .order_by(func.lower(Employee.name))
        .all()
    )
    return [{"id": e.id, "name": e.name, "role": slug, "phone": e.phone} for e, slug in rows]


@router.get("", response_model=EmployeeListResponse, summary="List employees")
def list_employees(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    role: str | None = Query(None, pattern=r"^[a-z][a-z0-9_]{1,39}$", description="Role slug, e.g. sales_executive"),
    is_active: bool | None = None,
    search: str | None = Query(None, max_length=100, description="Matches name, email or phone"),
    db: Session = Depends(get_db),
    viewer: Employee = Depends(get_current_user),
    platform: str = Depends(client_platform),
):
    """Paged list of employees with optional filters for role, active status and search.
    Admin and manager can list from any client. Other roles may only list from the mobile app.
    """
    viewer_slug = _viewer_slug(viewer)
    if viewer_slug not in ("admin", "manager") and platform != "mobile":
        raise HTTPException(status_code=403, detail="You do not have permission to perform this action")

    hidden_ids = _hidden_role_ids(db, viewer)
    q = db.query(Employee).filter(Employee.deleted_at.is_(None))
    if hidden_ids:
        q = q.filter(Employee.role_id.not_in(hidden_ids))

    if role:
        # Filter by role slug — look up the role_id first
        role_row = db.query(Role.id).filter(Role.slug == role).first()
        if role_row:
            q = q.filter(Employee.role_id == role_row.id)
        else:
            # Unknown slug → empty result
            return EmployeeListResponse(items=[], total=0, skip=skip, limit=limit)

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
    # Attach role slugs for the response (EmployeeResponse expects role_slug)
    slug_map: dict[int, str] = {
        r.id: r.slug
        for r in db.query(Role).all()
    }
    items = []
    for emp in rows:
        resp = EmployeeResponse.model_validate(emp, from_attributes=True)
        resp.role_slug = slug_map.get(emp.role_id)
        items.append(resp)

    return EmployeeListResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/{employee_id}", response_model=EmployeeResponse, summary="Get an employee")
def get_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    viewer: Employee = Depends(get_current_user),
):
    """One employee by id. Sales executives may only fetch their own record."""
    employee = _get_employee_or_404(db, employee_id, viewer=viewer)
    viewer_slug = _viewer_slug(viewer)
    if viewer.id != employee_id and viewer_slug not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="You do not have permission to view this employee")
    resp = EmployeeResponse.model_validate(employee, from_attributes=True)
    resp.role_slug = _load_slug(employee, db)
    return resp


@router.post("", response_model=EmployeeCreateResponse, status_code=201, summary="Create an employee")
def create_employee(
    body: EmployeeCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(manage_employees),
):
    """Admin or manager creates an employee. The role must exist and be active.
    Returns 409 for duplicate email/phone, 422 for unknown role.
    """
    role_row = _ensure_role_assignable(db, body.role, actor=actor)

    if _email_taken(db, body.email):
        raise HTTPException(status_code=409, detail=DUPLICATE_EMAIL)
    if body.phone and _phone_taken(db, body.phone):
        raise HTTPException(status_code=409, detail=DUPLICATE_PHONE)

    # body.role is a slug string; Employee needs role_id (integer FK)
    data = body.model_dump(exclude={"password", "role"})
    employee = Employee(**data, role_id=role_row.id, password_hash=get_password_hash(body.password))
    db.add(employee)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        detail = DUPLICATE_PHONE if "phone" in str(exc.orig).lower() else DUPLICATE_EMAIL
        raise HTTPException(status_code=409, detail=detail)

    db.refresh(employee)

    mail_result = send_employee_credentials(
        employee.name,
        employee.email,
        body.password,
        body.role,  # slug string for the email template
    )

    resp = EmployeeResponse.model_validate(employee, from_attributes=True)
    resp.role_slug = body.role
    return EmployeeCreateResponse(
        **resp.model_dump(),
        credentials_email=mail_result,
    )


@router.put("/{employee_id}", response_model=EmployeeResponse, summary="Update an employee")
def update_employee(
    employee_id: int,
    body: EmployeeUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(manage_employees),
):
    """Admin or manager edits an employee. Only the sent fields are changed."""
    employee = _get_employee_or_404(db, employee_id, viewer=actor)
    data = body.model_dump(exclude_unset=True)

    for field in ("name", "email", "role", "is_active", "password"):
        if field in data and data[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null")

    # If role is being changed, validate the new slug and get its ID
    new_role_id: int | None = None
    if "role" in data:
        current_slug = _load_slug(employee, db)
        if data["role"] != current_slug:
            role_row = _ensure_role_assignable(db, data["role"], actor=actor)
            new_role_id = role_row.id

    actor_slug = _viewer_slug(actor)
    if actor.id == employee.id and (
        data.get("is_active") is False or new_role_id is not None
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

    # Apply all fields except role (which needs special handling)
    role_slug_sent = data.pop("role", None)
    for field, value in data.items():
        setattr(employee, field, value)

    # Apply the new role_id if role was changed
    if new_role_id is not None:
        employee.role_id = new_role_id

    if data.get("is_active") is False:
        db.query(FcmDevice).filter(FcmDevice.employee_id == employee.id).delete(synchronize_session=False)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        detail = DUPLICATE_PHONE if "phone" in str(exc.orig).lower() else DUPLICATE_EMAIL
        raise HTTPException(status_code=409, detail=detail)

    db.refresh(employee)
    if password_changed:
        mail_result = send_password_changed_notice(employee.name, employee.email)
        if not mail_result["sent"]:
            logger.warning(
                "Password change notice not delivered to %s: %s",
                employee.email,
                mail_result["message"],
            )

    resp = EmployeeResponse.model_validate(employee, from_attributes=True)
    resp.role_slug = role_slug_sent if new_role_id is not None else _load_slug(employee, db)
    return resp


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
    employee.phone = None
    db.query(FcmDevice).filter(FcmDevice.employee_id == employee.id).delete(synchronize_session=False)
    db.commit()


class _FcmTokenBody(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    platform: str | None = Field(default=None, max_length=20)
    device_id: str | None = Field(default=None, max_length=255)

    @field_validator("token", mode="before")
    @classmethod
    def strip_token(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Token must not be blank or whitespace")
        return v


@router.post("/me/fcm-token", summary="Register or update FCM device token")
def register_fcm_token(
    body: _FcmTokenBody,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Store the caller's FCM push token. Called by web and mobile after login."""
    token = body.token
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
    current_user.fcm_token = token
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return {"ok": True}


@router.delete("/me/fcm-token", summary="Unregister an FCM device token")
def unregister_fcm_token(
    body: _FcmTokenBody,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Remove one browser/phone registration, normally called on logout."""
    token = body.token
    db.query(FcmDevice).filter(
        FcmDevice.employee_id == current_user.id,
        FcmDevice.token == token,
    ).delete(synchronize_session=False)
    if current_user.fcm_token == token:
        current_user.fcm_token = None
    db.commit()
    return {"ok": True}
