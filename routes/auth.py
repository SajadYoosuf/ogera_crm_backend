
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from database import get_db
from models import Employee
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


@router.post("/login", response_model=LoginResponse, summary="Log in")
def login(
    body: LoginRequest,
    db: Session = Depends(get_db),
):
    """Sign in with email and password. Returns a bearer token (valid 5 days by default) and the employee's profile.
    Returns 401 for an unknown email or a wrong password, and 403 if the account is inactive or deleted.
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
