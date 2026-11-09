
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr, Field, field_validator
from config import PASSWORD_MAX_LENGTH
from database import get_db
from models import Employee
from auth import HEAD_OFFICE_ROLES, verify_password, create_access_token, get_current_user, get_password_hash
from request_context import client_platform
from mailer import send_password_changed_notice
from schemas import EmployeeResponse
from schemas.employee import _validate_password

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def _mask_email(email: str) -> str:
    """
    Masks an email for safe production logging.
    "admin@ogera.com" → "a***@ogera.com"

    Why: logs may be stored or viewed by multiple people.
    Showing a***@domain.com is enough to confirm whether the
    right email was typed without exposing the full address.
    """
    local, sep, domain = email.partition("@")
    if not sep:
        return "***"
    return f"{local[:1]}***@{domain}"

DUPLICATE_EMAIL = "An employee with this email already exists"


class LoginRequest(BaseModel):
    # EmailStr: Pydantic validates the format before the handler runs.
    # A garbage string like "not-an-email" gets a 422 instead of wasting a DB query.
    email: EmailStr

    # Cap matches the creation policy so login and signup are consistent.
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    employee: EmployeeResponse


@router.post("/login", response_model=LoginResponse, summary="Log in")
def login(
    request: Request,
    body: LoginRequest,
    db: Session = Depends(get_db),
    platform: str = Depends(client_platform),
):
    
    """Sign in with email and password. Returns a bearer token (valid 5 days by default) and the employee's profile.
    Returns 401 for an unknown email or a wrong password, and 403 if the account is inactive or deleted,
    or if a role other than admin or manager signs in on the website.
    Send the token as `Authorization: Bearer <token>` on every later request.
    """
    client_ip = request.client.host if request.client else "unknown"

    # Normalize email: lowercase + strip whitespace so "Admin@X.COM " matches "admin@x.com".
    # EmailStr already validates format; we normalize before the DB query.
    email = body.email.strip().lower()

    # Mask email for logs: "admin@ogera.com" → "a***@ogera.com"
    # Full email is not needed in logs and is personal data.
    masked = _mask_email(email)
    logger.info("LOGIN REQUEST: email=%s platform=%s ip=%s", masked, platform, client_ip)

    employee = db.query(Employee).filter(Employee.email == email).first()

    if not employee:
        logger.warning("LOGIN FAILED — email not found: email=%s platform=%s ip=%s", masked, platform, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Verify password BEFORE checking active/deleted status.
    # This way a wrong password always returns 401 "Invalid email or password",
    # even for deactivated accounts — the attacker learns nothing about account
    # status unless they already know the correct password.
    if not verify_password(body.password, employee.password_hash):
        logger.warning("LOGIN FAILED — wrong password: email=%s employee_id=%d platform=%s ip=%s",
                       masked, employee.id, platform, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Password is correct. Now safe to reveal why login is blocked.
    if employee.is_active != 1 or employee.deleted_at is not None:
        logger.warning("LOGIN BLOCKED — account inactive/deleted: email=%s employee_id=%d ip=%s",
                       masked, employee.id, client_ip)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Employee account is inactive",
        )

    # The website is for admin and manager. Sales executives work from the mobile app.
    if platform == "web" and employee.role not in HEAD_OFFICE_ROLES:
        logger.warning("LOGIN BLOCKED — wrong platform: email=%s role=%s tried=web ip=%s",
                       masked, employee.role, client_ip)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please sign in on the Ogera mobile app",
        )

    # Pass password_hash so the token embeds a fingerprint.
    # If the employee changes their password later, this fingerprint won't match
    # and get_current_user() will reject the old token (forces re-login).
    access_token = create_access_token(employee.id, employee.password_hash)

    logger.info("LOGIN OK: employee_id=%d name=%r role=%s platform=%s ip=%s",
                employee.id, employee.name, employee.role, platform, client_ip)

    return LoginResponse(
        access_token=access_token,
        employee=EmployeeResponse.model_validate(employee, from_attributes=True),
    )


@router.get("/me", response_model=EmployeeResponse, summary="Get my profile")
def get_profile(current_user: Employee = Depends(get_current_user)):
    """Returns the profile of the signed-in employee. Use it to restore the session when the app starts.
    Returns 401 if the token is missing or expired, or the employee has been deleted.
    """
    return EmployeeResponse.model_validate(current_user, from_attributes=True)


def _attached_me(db: Session, current_user: Employee) -> Employee:
    # get_current_user returns an employee whose session is already closed, so load it again to change it.
    me = db.query(Employee).filter(Employee.id == current_user.id, Employee.deleted_at.is_(None)).first()
    if me is None:
        raise HTTPException(status_code=401, detail="Session expired, please sign in again")
    return me


class ProfileUpdate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    email: EmailStr | None = None
    # Required only when the email changes.
    current_password: str | None = Field(default=None, max_length=PASSWORD_MAX_LENGTH)

    @field_validator("name", mode="after")
    @classmethod
    def strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Name must not be blank")
        return v

    @field_validator("email", mode="after")
    @classmethod
    def lowercase_email(cls, v: str | None) -> str | None:
        return v.lower() if v else v


@router.patch("/me", response_model=EmployeeResponse, summary="Update my name or email")
def update_profile(
    body: ProfileUpdate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Changes the signed-in employee's name, and/or email. A new email needs `current_password`.
    Returns 400 for a wrong password, 409 if the email belongs to someone else.
    """
    me = _attached_me(db, current_user)
    data = body.model_dump(exclude_unset=True)

    # Reject empty requests — PATCH with no fields is a client mistake, not a no-op.
    if not data:
        raise HTTPException(status_code=400, detail="Provide at least one field to update")

    if data.get("name") is not None:
        me.name = data["name"]

    new_email = data.get("email")
    if new_email:
        if new_email == me.email:
            # Sending the same email is a no-op. Tell the client explicitly rather
            # than silently succeeding — this was previously allowing the request
            # through without the current_password check.
            raise HTTPException(status_code=400, detail="The email address is the same as your current one")
        if not data.get("current_password"):
            raise HTTPException(status_code=400, detail="Enter your current password to change your email")
        if not verify_password(data["current_password"], me.password_hash):
            raise HTTPException(status_code=400, detail="Incorrect password")
        taken = db.query(Employee).filter(Employee.email == new_email, Employee.id != me.id).first()
        if taken is not None:
            raise HTTPException(status_code=409, detail=DUPLICATE_EMAIL)
        me.email = new_email
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_EMAIL)
    db.refresh(me)
    return EmployeeResponse.model_validate(me, from_attributes=True)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=PASSWORD_MAX_LENGTH)
    new_password: str

    @field_validator("new_password", mode="after")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return _validate_password(v)


@router.post("/change-password", summary="Change my password")
def change_password(
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Changes the signed-in employee's password. Needs the current one. The new one must be 6 to 15 characters
    with a letter and a digit. A notice is emailed; a failed email does not undo the change.
    """
    me = _attached_me(db, current_user)
    if not verify_password(body.current_password, me.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if verify_password(body.new_password, me.password_hash):
        raise HTTPException(status_code=400, detail="New password must be different from the current one")
    me.password_hash = get_password_hash(body.new_password)
    db.commit()
    db.refresh(me)
    send_password_changed_notice(me.name, me.email)
    # Issue a fresh token with the new password fingerprint so the employee
    # stays logged in after the change. Without this, their next request would
    # fail with 401 because the old token's phf no longer matches.
    new_token = create_access_token(me.id, me.password_hash)
    return {"message": "Password changed", "access_token": new_token, "token_type": "bearer"}
