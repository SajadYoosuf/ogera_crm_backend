from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer
from config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES
from database import SessionLocal
from models import Employee

# Password hashing
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# HTTP Bearer scheme
security = HTTPBearer()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password."""
    return pwd_context.hash(password[:72])  # Truncate to 72 bytes for bcrypt compatibility


def create_access_token(employee_id: int, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    if expires_delta is None:
        expires_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    expire = datetime.utcnow() + expires_delta
    to_encode = {"sub": str(employee_id), "exp": expire}
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def get_current_user(credentials = Depends(security)) -> Employee:
    """Verify JWT token and return current employee."""
    token = credentials.credentials

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        employee_id: str = payload.get("sub")
        if employee_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    db = SessionLocal()
    try:
        employee = db.query(Employee).filter(Employee.id == int(employee_id)).first()
        if employee is None or employee.deleted_at is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Employee not found",
            )
        return employee
    finally:
        db.close()


# The head-office roles. They use the website; everyone else uses the mobile app.
HEAD_OFFICE_ROLES = ("admin", "manager")


def require_roles(*roles):
    """Dependency factory: allow only employees whose role is in `roles`."""
    allowed = {r.value if hasattr(r, "value") else r for r in roles}

    def checker(current_user: Employee = Depends(get_current_user)) -> Employee:
        if not current_user.is_active or current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )
        return current_user

    return checker


# The one role check the backend keeps: only admin and manager may manage employees, roles and settings.
require_admin_or_manager = require_roles(*HEAD_OFFICE_ROLES)
