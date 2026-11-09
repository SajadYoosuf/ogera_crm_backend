import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session
from config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES
from database import get_db
from models import Employee

logger = logging.getLogger(__name__)

# Password hashing — argon2 (no 72-byte truncation needed, unlike bcrypt)
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# auto_error=False: when no Authorization header is sent, FastAPI would return 403
# "Not authenticated" by default. We handle the None case ourselves and return 401,
# which is the correct HTTP status for "you are not authenticated".
security = HTTPBearer(auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password using argon2. No truncation — argon2 handles any length."""
    return pwd_context.hash(password)


def _password_fingerprint(password_hash: str) -> str:
    """
    Returns a short, one-way fingerprint of the stored password hash.

    Why: JWTs are stateless — once issued, the server cannot revoke them.
    If an employee changes their password, their old token would still work
    for the remaining 5-day lifetime. This fingerprint lets us detect that.

    How it works:
      1. At login we SHA-256 the argon2 hash string and embed the first 16 hex
         chars in the JWT as the "phf" (password hash fingerprint) claim.
      2. On every protected request, get_current_user() re-computes the
         fingerprint from the DB and compares it to the JWT's "phf".
      3. If the password was changed after the token was issued, the argon2 hash
         changed → the fingerprint no longer matches → 401, re-login required.

    We hash-of-hash (SHA-256 of argon2 string) so the JWT never contains any
    part of the actual argon2 hash, even in truncated form.
    """
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def create_access_token(
    employee_id: int,
    password_hash: str,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a JWT access token.

    Embeds a password fingerprint (phf) so the token becomes invalid
    the moment the employee changes their password.
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    expire = datetime.utcnow() + expires_delta
    to_encode = {
        "sub": str(employee_id),
        "exp": expire,
        "phf": _password_fingerprint(password_hash),
    }
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    credentials=Depends(security),
    db: Session = Depends(get_db),
) -> Employee:


    """Verify JWT token and return current employee."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    token = credentials.credentials

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        employee_id = payload.get("sub")
        if employee_id is None:
            logger.warning("TOKEN REJECTED — no 'sub' claim in payload")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )
        employee_id = int(employee_id)
    except (JWTError, TypeError, ValueError) as exc:
        logger.warning("TOKEN REJECTED — decode error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    employee = db.scalar(
        select(Employee).where(Employee.id == employee_id)
    )
    if employee is None or employee.deleted_at is not None:
        logger.warning("TOKEN REJECTED — employee not found or deleted: id=%s", employee_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    if not employee.is_active:
        logger.warning("TOKEN REJECTED — account deactivated: id=%s", employee_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been deactivated. Please contact your administrator.",
        )

    # Validate password fingerprint — rejects tokens issued before a password change.
    # Tokens issued before this feature was added won't have "phf" → force re-login.
    token_phf = payload.get("phf")
    current_phf = _password_fingerprint(employee.password_hash)
    if token_phf != current_phf:
        logger.warning(
            "TOKEN REJECTED — password fingerprint mismatch (password changed or old token): employee_id=%s",
            employee_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your Login  has expired. Please log in again to continue.",
        )

    return employee


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
