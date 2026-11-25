"""
Authentication routes — login, profile, password change.

Login flow:
  1. Query public.employee_tenant_map by email → get tenant_id
  2. Query public.tenants by tenant_id → get schema_name + company info
  3. Open session for that schema, verify password, return token + company + employee
"""
import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr, Field, field_validator

from config import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH
from database import get_public_db, get_session_for_schema
from models import Employee, EmployeeLookup, Role, Tenant
from auth import (
    verify_password,
    create_access_token,
    get_current_user,
    get_password_hash,
)
from mailer import send_password_changed_notice
from routes.employees import DUPLICATE_EMAIL, _email_taken
from schemas import EmployeeResponse
from schemas.employee import _validate_password

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def _mask_email(email: str) -> str:
    local, sep, domain = email.partition("@")
    if not sep:
        return "***"
    return f"{local[:1]}***@{domain}"


def _extract_tenant_from_employee(employee: Employee) -> str:
    tenant = getattr(employee, "_tenant_schema", None)
    if not tenant:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return tenant


class CompanyResponse(BaseModel):
    id: int
    name: str
    logo_url: str | None = None
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    company: CompanyResponse
    employee: EmployeeResponse


class MeResponse(BaseModel):
    company: CompanyResponse
    employee: EmployeeResponse


@router.post("/login", response_model=LoginResponse, summary="Log in")
def login(
    request: Request,
    body: LoginRequest,
    db_public: Session = Depends(get_public_db),
):
    """
    401 — wrong email or password
    403 — account inactive or deleted
    503 — database unavailable
    """
    client_ip = request.client.host if request.client else "unknown"
    email = body.email.strip().lower()
    logger.info("LOGIN REQUEST: email=%s ip=%s", _mask_email(email), client_ip)

    try:
        lookup = db_public.scalar(
            select(EmployeeLookup).where(EmployeeLookup.email == email)
        )
        if not lookup:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

        tenant = db_public.scalar(
            select(Tenant).where(Tenant.id == lookup.tenant_id)
        )
        if not tenant:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="Server unavailable, please retry in a moment")
    except OperationalError as exc:
        logger.error("LOGIN — public DB error: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Server unavailable, please retry in a moment")

    try:
        with get_session_for_schema(tenant.schema_name) as db_tenant:
            employee = db_tenant.scalar(
                select(Employee).where(Employee.email == email)
            )

            if not employee or not verify_password(body.password, employee.password_hash):
                logger.warning("LOGIN FAILED: email=%s ip=%s", _mask_email(email), client_ip)
                raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                    detail="Invalid email or password")

            if not employee.is_active or employee.deleted_at is not None:
                raise HTTPException(status.HTTP_403_FORBIDDEN,
                                    detail="Employee account is inactive")

            role = db_tenant.scalar(select(Role).where(Role.id == employee.role_id))
            object.__setattr__(employee, "role_slug", role.slug if role else None)

            access_token = create_access_token(
                employee.id, employee.password_hash, tenant.schema_name
            )
            logger.info("LOGIN OK: employee_id=%d ip=%s", employee.id, client_ip)
            return LoginResponse(
                access_token=access_token,
                company=CompanyResponse.model_validate(tenant),
                employee=EmployeeResponse.model_validate(employee, from_attributes=True),
            )

    except (OperationalError, ValueError) as exc:
        logger.error("LOGIN — tenant DB error: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Server unavailable, please retry in a moment")


@router.get("/me", response_model=MeResponse, summary="Get my profile")
def get_profile(
    current_user: Employee = Depends(get_current_user),
    db_public: Session = Depends(get_public_db),
):
    tenant_schema = _extract_tenant_from_employee(current_user)
    try:
        tenant = db_public.scalar(
            select(Tenant).where(Tenant.schema_name == tenant_schema)
        )
    except OperationalError as exc:
        logger.error("GET PROFILE — public DB error: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Server unavailable, please retry in a moment")
    if not tenant:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Server unavailable, please retry in a moment")
    return MeResponse(
        company=CompanyResponse.model_validate(tenant),
        employee=EmployeeResponse.model_validate(current_user, from_attributes=True),
    )


class ProfileUpdate(BaseModel):
    """My account: name, and email (email needs current_password). Only the sent fields change."""
    name: str | None = Field(default=None, max_length=120)
    email: EmailStr | None = None
    current_password: str | None = Field(default=None, max_length=PASSWORD_MAX_LENGTH)


@router.patch("/me", response_model=EmployeeResponse, summary="Update my name or email")
def update_profile(
    body: ProfileUpdate,
    current_user: Employee = Depends(get_current_user),
    db_public: Session = Depends(get_public_db),
):
    """
    400 — email change without the right current password
    409 — email already used by another employee
    503 — database unavailable
    """
    tenant_schema = _extract_tenant_from_employee(current_user)
    data = body.model_dump(exclude_unset=True)
    for field in ("name", "email"):
        if field in data and data[field] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{field} cannot be null")
    if "name" in data:
        data["name"] = data["name"].strip()
        if not data["name"]:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Name is required")

    try:
        tenant = db_public.scalar(select(Tenant).where(Tenant.schema_name == tenant_schema))
        if tenant is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="Server unavailable, please retry in a moment")

        with get_session_for_schema(tenant_schema) as db:
            me = db.scalar(
                select(Employee).where(Employee.id == current_user.id, Employee.deleted_at.is_(None))
            )
            if me is None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                    detail="Your login has expired. Please sign in again to continue.")

            old_email = me.email
            new_email = data["email"].strip().lower() if "email" in data else None
            email_changes = new_email is not None and new_email != old_email.lower()
            if email_changes:
                if not body.current_password or not verify_password(body.current_password, me.password_hash):
                    raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                        detail="Enter your current password to change your email")
                if _email_taken(db, new_email, exclude_id=me.id) or db_public.scalar(
                    select(EmployeeLookup).where(EmployeeLookup.email == new_email)
                ):
                    raise HTTPException(status.HTTP_409_CONFLICT, detail=DUPLICATE_EMAIL)

            # Login finds the account through public.employee_lookup, so move that row first.
            if email_changes:
                db_public.execute(
                    update(EmployeeLookup).where(EmployeeLookup.email == old_email).values(email=new_email)
                )
                db_public.flush()
            if "name" in data:
                me.name = data["name"]
            if email_changes:
                me.email = new_email
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                db_public.rollback()
                raise HTTPException(status.HTTP_409_CONFLICT, detail=DUPLICATE_EMAIL)
            except SQLAlchemyError as exc:
                db.rollback()
                db_public.rollback()
                logger.error("UPDATE PROFILE — commit failed: %s", exc)
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                    detail="Could not save your changes. Please try again in a moment.")
            db_public.commit()

            db.refresh(me)
            resp = EmployeeResponse.model_validate(me, from_attributes=True)
            resp.role_slug = getattr(current_user, "role_slug", None)
            return resp

    except OperationalError as exc:
        logger.error("UPDATE PROFILE — DB error: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Server unavailable, please retry in a moment")


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
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
            # Same-password check intentionally runs only after current_password is verified,
            # so a wrong current_password always gets the "doesn't match" error first.
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

            try:
                send_password_changed_notice(me.name, me.email)
            except Exception:
                logger.warning("CHANGE PASSWORD — notice email failed for employee_id=%d", me.id)

            new_token = create_access_token(me.id, me.password_hash, tenant_schema)
            return {
                "message": "Password changed",
                "access_token": new_token,
                "token_type": "bearer",
            }

    except (OperationalError, ValueError) as exc:
        logger.error("CHANGE PASSWORD — DB error: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Server unavailable, please retry in a moment")
