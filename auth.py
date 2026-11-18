"""
JWT helpers, password hashing, and FastAPI auth dependencies.

Each JWT contains three claims:
  sub    — employee ID
  tenant — PostgreSQL schema name (e.g. "ventino")
  phf    — first 16 hex chars of SHA-256(password_hash)
           If the employee changes their password, phf changes and
           the old token is rejected on the next request.
"""
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES
from database import get_session_for_schema, _validate_schema_name
from models import Employee, Role

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# auto_error=False so we can return 401 instead of FastAPI's default 403.
security = HTTPBearer(auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def _password_fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def create_access_token(
    employee_id: int,
    password_hash: str,
    tenant_schema: str,
    expires_delta: Optional[timedelta] = None,
) -> str:
    _validate_schema_name(tenant_schema)
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    payload = {
        "sub": str(employee_id),
        "exp": expire,
        "phf": _password_fingerprint(password_hash),
        "tenant": tenant_schema,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(credentials=Depends(security)) -> Employee:
    """
    FastAPI dependency — decode JWT and return the authenticated employee.

    Raises 401 if the token is missing, invalid, or the password changed.
    Raises 403 if the account is deactivated.
    Raises 503 if the database is unavailable.
    """
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        employee_id_raw = payload.get("sub")
        tenant_schema = payload.get("tenant")
        token_phf = payload.get("phf")

        if employee_id_raw is None:
            raise ValueError("Missing sub claim")
        employee_id = int(employee_id_raw)
    except (JWTError, TypeError, ValueError) as exc:
        logger.warning("TOKEN REJECTED — decode error: %s", exc)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if not tenant_schema:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        _validate_schema_name(tenant_schema)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        with get_session_for_schema(tenant_schema) as db:
            employee = db.scalar(select(Employee).where(Employee.id == employee_id))
            role_slug: Optional[str] = None
            if employee:
                role = db.scalar(select(Role).where(Role.id == employee.role_id))
                role_slug = role.slug if role else None
    except OperationalError as exc:
        logger.error("TOKEN — DB error: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Database unavailable, please retry in a moment")

    if employee is None or employee.deleted_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if not employee.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="Your account has been deactivated. Please contact your administrator.")

    current_phf = _password_fingerprint(employee.password_hash)
    if not token_phf or token_phf != current_phf:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail="Your login has expired. Please log in again to continue.")

    # Attach transient attributes. object.__setattr__ is used because SQLModel
    # raises ValueError when you set an attribute that is not a declared field.
    object.__setattr__(employee, "_tenant_schema", tenant_schema)
    object.__setattr__(employee, "_role_slug", role_slug)
    object.__setattr__(employee, "role_slug", role_slug)

    return employee


# Roles that can manage employees, settings, and other admin tasks.
HEAD_OFFICE_ROLES = ("admin", "manager")


def require_roles(*roles):
    """
    Returns a FastAPI dependency that allows only employees with the given role slugs.

    Usage:
        Depends(require_roles("admin", "manager"))
    """
    allowed = {r.value if hasattr(r, "value") else r for r in roles}

    def checker(current_user: Employee = Depends(get_current_user)) -> Employee:
        role_slug = getattr(current_user, "_role_slug", None)
        if not current_user.is_active or role_slug not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                detail="You do not have permission to perform this action")
        return current_user

    return checker


require_admin_or_manager = require_roles(*HEAD_OFFICE_ROLES)
