"""
Authentication routes — login, profile read/update, password change.

Login flow (multi-tenant):
  1. Query public.employee_tenant_map by email to find the tenant schema.
  2. Open a session for that schema, verify password.
  3. Return a JWT containing the employee ID, schema name, and password fingerprint.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr, Field, field_validator

from config import PASSWORD_MAX_LENGTH
from database import get_public_db, get_session_for_schema
from models import Employee, EmployeeLookup, Role
from auth import (
    verify_password,
    create_access_token,
    get_current_user,
    get_password_hash,
)
from mailer import send_password_changed_notice
from schemas import EmployeeResponse
from schemas.employee import _validate_password

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def _mask_email(email: str) -> str:
    """Returns "a***@domain.com" — safe for logging."""
    local, sep, domain = email.partition("@")
    if not sep:
        return "***"
    return f"{local[:1]}***@{domain}"


def _extract_tenant_from_employee(employee: Employee) -> str:
    """Read the tenant schema that get_current_user() attached to the employee object."""
    tenant = getattr(employee, "_tenant_schema", None)
    if not tenant:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return tenant


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    employee: EmployeeResponse

# first checking the employee email id with public schema employee lookup table then matching with employee email
#then check the tenant schema id and name then go to that tenant with search path then going to that tenant based employees
#table then find the email and password check it and signin and the return access token jwt also contain tenant
#schema name
@router.post("/login", response_model=LoginResponse, summary="Log in")
def login(
    request: Request,
    body: LoginRequest,
    db_public: Session = Depends(get_public_db),
):
    """
    Sign in with email and password. Returns a bearer token and the employee's profile.

    401 — wrong email or password
    403 — account inactive or deleted
    503 — database unavailable
    """
    client_ip = request.client.host if request.client else "unknown"
    email = body.email.strip().lower()
    masked = _mask_email(email)
    logger.info("LOGIN REQUEST: email=%s ip=%s", masked, client_ip)

    try:
        lookup = db_public.scalar(
            select(EmployeeLookup).where(EmployeeLookup.email == email)
        )
    except OperationalError as exc:
        logger.error("LOGIN — DB error on employee_tenant_map: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Server unavailable, please retry in a moment")

    if not lookup:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    try:
        with get_session_for_schema(lookup.schema_name) as db_tenant:
            employee = db_tenant.scalar(
                select(Employee).where(Employee.email == email)
            )

            if not employee or not verify_password(body.password, employee.password_hash):
                logger.warning("LOGIN FAILED — wrong password: email=%s ip=%s", masked, client_ip)
                raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                    detail="Invalid email or password")

            if not employee.is_active or employee.deleted_at is not None:
                raise HTTPException(status.HTTP_403_FORBIDDEN,
                                    detail="Employee account is inactive")

            role = db_tenant.scalar(select(Role).where(Role.id == employee.role_id))
            object.__setattr__(employee, "role_slug", role.slug if role else None)

            access_token = create_access_token(
                employee.id, employee.password_hash, lookup.schema_name
            )
            logger.info("LOGIN OK: employee_id=%d ip=%s", employee.id, client_ip)
            return LoginResponse(
                access_token=access_token,
                employee=EmployeeResponse.model_validate(employee, from_attributes=True),
            )

    except (OperationalError, ValueError) as exc:
        logger.error("LOGIN — DB error on schema %s: %s", lookup.schema_name, exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Database unavailable, please retry in a moment")


@router.get("/me", response_model=EmployeeResponse, summary="Get my profile")
def get_profile(current_user: Employee = Depends(get_current_user)):
    return EmployeeResponse.model_validate(current_user, from_attributes=True)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=6, max_length=PASSWORD_MAX_LENGTH)
    new_password: str = Field(max_length=PASSWORD_MAX_LENGTH)

    @field_validator("new_password", mode="after")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return _validate_password(v)


@router.post("/change-password", summary="Change my password")
def change_password(
    body: ChangePasswordRequest,
    current_user: Employee = Depends(get_current_user),
):
    """
    Change password. Requires the current password.
    Returns a fresh token so the employee stays signed in.

    400 — wrong current password, or same as new
    503 — database unavailable
    """
    tenant_schema = _extract_tenant_from_employee(current_user)

    try:
        with get_session_for_schema(tenant_schema) as db:
            me = db.scalar(
                select(Employee).where(
                    Employee.id == current_user.id,
                    Employee.deleted_at.is_(None),
                )
            )
            if me is None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                    detail="Your login has expired. Please sign in again to continue.")

            if not verify_password(body.current_password, me.password_hash):
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    detail="The password you entered doesn't match your current one.")
            if body.new_password == body.current_password:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    detail="New password must be different from the current one")

            me.password_hash = get_password_hash(body.new_password)

            try:
                db.commit()
            except SQLAlchemyError as exc:
                db.rollback()
                logger.error("CHANGE PASSWORD — commit failed: %s", exc)
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                    detail="Could not save your new password. Please try again in a moment.")

            db.refresh(me)
            send_password_changed_notice(me.name, me.email)

            new_token = create_access_token(me.id, me.password_hash, tenant_schema)
            return {
                "message": "Password changed",
                "access_token": new_token,
                "token_type": "bearer",
            }

    except (OperationalError, ValueError) as exc:
        logger.error("CHANGE PASSWORD — DB error: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Database unavailable, please retry in a moment")
