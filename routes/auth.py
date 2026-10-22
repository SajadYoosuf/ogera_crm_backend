
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr, Field, field_validator
from database import get_db
from models import Employee
from auth import HEAD_OFFICE_ROLES, verify_password, create_access_token, get_current_user, get_password_hash
from request_context import client_platform
from mailer import send_password_changed_notice
from schemas import EmployeeResponse
from schemas.employee import _validate_password

router = APIRouter(prefix="/auth", tags=["auth"])

DUPLICATE_EMAIL = "An employee with this email already exists"


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    employee: EmployeeResponse


@router.post("/login", response_model=LoginResponse, summary="Log in")
def login(
    body: LoginRequest,
    db: Session = Depends(get_db),
    platform: str = Depends(client_platform),
):
    """Sign in with email and password. Returns a bearer token (valid 5 days by default) and the employee's profile.
    Returns 401 for an unknown email or a wrong password, and 403 if the account is inactive or deleted,
    or if a role other than admin or manager signs in on the website.
    Send the token as `Authorization: Bearer <token>` on every later request.
    """
    employee = db.query(Employee).filter(Employee.email == body.email).first()

    if not employee:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not verify_password(body.password, employee.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not employee.is_active or employee.deleted_at:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Employee account is inactive",
        )

    # The website is for admin and manager. Sales executives work from the mobile app.
    if platform == "web" and employee.role not in HEAD_OFFICE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please sign in on the Ogera mobile app",
        )

    access_token = create_access_token(employee.id)

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
        raise HTTPException(status_code=401, detail="Employee not found")
    return me


class ProfileUpdate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    email: EmailStr | None = None
    # Required only when the email changes.
    current_password: str | None = None

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
    if data.get("name") is not None:
        me.name = data["name"]
    if data.get("email") and data["email"] != me.email:
        if not data.get("current_password") or not verify_password(data["current_password"], me.password_hash):
            raise HTTPException(status_code=400, detail="Enter your current password to change your email")
        taken = db.query(Employee).filter(Employee.email == data["email"], Employee.id != me.id).first()
        if taken is not None:
            raise HTTPException(status_code=409, detail=DUPLICATE_EMAIL)
        me.email = data["email"]
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_EMAIL)
    db.refresh(me)
    return EmployeeResponse.model_validate(me, from_attributes=True)


class ChangePasswordRequest(BaseModel):
    current_password: str
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
    send_password_changed_notice(me.name, me.email)
    return {"message": "Password changed"}
