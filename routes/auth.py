
import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from database import get_db
from models import Employee, EmployeePresence
from auth import verify_password, create_access_token, get_current_user
from schemas import EmployeeResponse

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    employee: EmployeeResponse


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    db: Session = Depends(get_db),
):
    """Login with email and password, return JWT token."""
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

    access_token = create_access_token(employee.id)

    return LoginResponse(
        access_token=access_token,
        employee=EmployeeResponse.model_validate(employee, from_attributes=True),
    )


@router.get("/me", response_model=EmployeeResponse)
def get_profile(current_user: Employee = Depends(get_current_user)):
    """Get current user profile."""
    return EmployeeResponse.model_validate(current_user, from_attributes=True)


class HeartbeatResponse(BaseModel):
    employee_id: int
    is_online: bool
    last_seen_at: datetime.datetime
    # A fresh token, so an employee who is still using the app is not logged out when the old one runs out.
    access_token: str
    token_type: str = "bearer"


@router.post("/heartbeat", response_model=HeartbeatResponse)
def heartbeat(
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Called by the app while it is in use. Records that the employee is online and returns a refreshed token.

    Presence is separate from the account's is_active flag: is_active is set by an admin, presence comes from this call.
    """
    if not current_user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Employee account is inactive")

    now = datetime.datetime.utcnow()
    presence = db.get(EmployeePresence, current_user.id)
    if presence is None:
        presence = EmployeePresence(employee_id=current_user.id, last_seen_at=now)
        db.add(presence)
    else:
        presence.last_seen_at = now
    db.commit()

    return HeartbeatResponse(
        employee_id=current_user.id,
        is_online=True,
        last_seen_at=now,
        access_token=create_access_token(current_user.id),
    )
