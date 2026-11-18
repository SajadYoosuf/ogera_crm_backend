"""Forgot password: email a one-time code to the employee's own address, check it, then set a new password.

Flow:
  POST /request  → looks up which company the email belongs to, emails a 6-digit OTP
  POST /verify   → checks the OTP, returns a short-lived reset JWT
  POST /reset    → uses the reset JWT to set a new password (token is single-use)

Multi-tenant note:
  Every endpoint first queries public.employee_tenant_map to find which PostgreSQL schema
  (company) this email belongs to, then opens a session for that schema to do the actual
  employee and OTP work.  This is the same two-step pattern used by POST /auth/login.

The reset JWT embeds the tenant schema name so /reset does not need a public lookup.
"""
import datetime
import hashlib
import hmac
import logging
import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from auth import pwd_context, verify_password
from config import (
    ALGORITHM,
    RESET_CODE_TTL_MINUTES,
    RESET_TOKEN_TTL_MINUTES,
    SECRET_KEY,
)
from database import get_public_db, get_session_for_schema, _validate_schema_name
from mailer import send_password_changed_notice, send_password_reset_code
from models import Employee, EmployeeLookup, PasswordResetCode, Tenant
from schemas.employee import _validate_password

router = APIRouter(prefix="/auth/password-reset", tags=["auth"])
logger = logging.getLogger(__name__)

CODE_TTL = datetime.timedelta(minutes=RESET_CODE_TTL_MINUTES)
RESET_TOKEN_TTL = datetime.timedelta(minutes=RESET_TOKEN_TTL_MINUTES)
RESET_PURPOSE = "password_reset"


# ── Pydantic request schemas ──────────────────────────────────────────────────

class EmailRequest(BaseModel):
    email: EmailStr


class VerifyRequest(BaseModel):
    email: EmailStr
    # Exactly 6 digits — Pydantic rejects blanks, letters, and strings of the wrong length
    # before the code ever reaches the HMAC comparison.
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class ResetRequest(BaseModel):
    reset_token: str = Field(min_length=1, max_length=512)
    new_password: str

    @field_validator("new_password", mode="after")
    @classmethod
    def password_strength(cls, v: str) -> str:
        # Delegates to the same validator used by EmployeeCreate / EmployeeUpdate.
        return _validate_password(v)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _resolve_schema(public_db: Session, email: str) -> str:
    """Find which tenant schema this email belongs to.

    Queries the shared public tables in two steps:
      1. public.employee_tenant_map  → email  → tenant_id
      2. public.tenants              → id     → schema_name  (e.g. 'ventino')

    Raises 404 if the email is not registered with any tenant.
    Raises 503 if the tenant row is somehow missing (data integrity problem).
    """
    email = email.strip().lower()
    try:
        lookup = public_db.scalar(
            select(EmployeeLookup).where(EmployeeLookup.email == email)
        )
    except OperationalError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server unavailable, please retry in a moment.",
        )
    if not lookup:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active account uses this email.",
        )
    tenant = public_db.scalar(
        select(Tenant).where(Tenant.id == lookup.tenant_id)
    )
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server unavailable, please retry in a moment.",
        )
    return tenant.schema_name


def _account_for_email(db: Session, email: str) -> Employee:
    """Find the active employee with this email in the tenant-scoped session.

    Call only after _resolve_schema has confirmed the tenant schema and
    get_session_for_schema has opened a session for it.
    """
    email = email.strip().lower()
    account = (
        db.query(Employee)
        .filter(
            func.lower(Employee.email) == email,
            Employee.is_active == 1,
            Employee.deleted_at.is_(None),
        )
        .first()
    )
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active account uses this email.",
        )
    return account


def _code_hash(employee_id: int, code: str) -> str:
    """HMAC-SHA256 fingerprint of the OTP.

    Why: we store only this hash in the database, never the plaintext code.
    If the database were leaked, an attacker would need the SECRET_KEY to do
    anything with the hashes.  hmac.compare_digest() in the verify step
    prevents timing attacks.
    """
    return hmac.new(SECRET_KEY.encode(), f"{employee_id}:{code}".encode(), hashlib.sha256).hexdigest()


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if len(local) <= 2:
        return f"{local[:1]}***@{domain}"
    return f"{local[0]}***{local[-1]}@{domain}"


def _password_fingerprint(password_hash: str) -> str:
    """Short fingerprint of the password hash embedded in the reset JWT.

    When the password changes, this fingerprint changes too, which makes any
    outstanding reset tokens invalid immediately (single-use guarantee).
    """
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def _active_code(db: Session, employee_id: int) -> PasswordResetCode | None:
    return (
        db.query(PasswordResetCode)
        .filter(
            PasswordResetCode.employee_id == employee_id,
            PasswordResetCode.consumed_at.is_(None),
        )
        .order_by(PasswordResetCode.id.desc())
        .first()
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/request", summary="Send a reset code")
def request_code(
    body: EmailRequest,
    db_public: Session = Depends(get_public_db),
):
    """Step 1 — sends a 6-digit code to the employee's registered email.

    How it works:
    - Finds which company schema the email belongs to (public lookup).
    - Marks any existing active codes as consumed so only one code is valid at a time.
    - Generates a new random 6-digit code, stores its HMAC hash (never the code itself).
    - Sends the code by email BEFORE committing so the DB row is rolled back if the
      mail server fails.  This guarantees the employee is never waiting for a code that
      was never actually sent.

    Errors:
      404 — no active account with this email
      503 — could not send the email (try again)
    """
    schema = _resolve_schema(db_public, body.email)

    try:
        with get_session_for_schema(schema) as db:
            account = _account_for_email(db, body.email)
            now = _now()

            # Consume all existing active codes so only the new one is valid.
            db.query(PasswordResetCode).filter(
                PasswordResetCode.employee_id == account.id,
                PasswordResetCode.consumed_at.is_(None),
            ).update({PasswordResetCode.consumed_at: now}, synchronize_session=False)

            code = f"{secrets.randbelow(10**6):06d}"
            db.add(
                PasswordResetCode(
                    employee_id=account.id,
                    code_hash=_code_hash(account.id, code),
                    expires_at=now + CODE_TTL,
                    created_at=now,
                )
            )

            # Send email BEFORE committing — if it fails, rollback wipes the new code row.
            result = send_password_reset_code(
                account.name,
                account.email,
                code,
                int(CODE_TTL.total_seconds() // 60),
            )
            if not result.get("sent"):
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=result.get("message", "Could not send reset code. Please try again."),
                )

            try:
                db.commit()
            except SQLAlchemyError as exc:
                db.rollback()
                logger.error("REQUEST CODE — commit failed: %s", exc)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Could not save reset code. Please try again.",
                )
    except OperationalError as exc:
        logger.error("REQUEST CODE — DB error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server unavailable, please retry in a moment.",
        )

    return {
        "email_masked": _mask_email(body.email.strip().lower()),
        "expires_in_seconds": int(CODE_TTL.total_seconds()),
    }


@router.post("/verify", summary="Check the reset code")
def verify_code(
    body: VerifyRequest,
    db_public: Session = Depends(get_public_db),
):
    """Step 2 — verifies the 6-digit code and returns a short-lived reset token.

    How it works:
    - Finds the active (unconsumed, unexpired) code for this employee.
    - Compares the submitted code against the stored HMAC hash.
    - On success, marks the code consumed and returns a JWT reset token that
      is valid for RESET_TOKEN_TTL_MINUTES.  The token embeds the tenant schema
      and a password fingerprint so /reset can work without another public lookup
      and so the token becomes invalid as soon as the password changes.

    Errors:
      404 — no active account with this email
      400 — no active code, expired code, or wrong code
    """
    schema = _resolve_schema(db_public, body.email)

    try:
        with get_session_for_schema(schema) as db:
            account = _account_for_email(db, body.email)
            row = _active_code(db, account.id)
            now = _now()

            if row is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No active code. Request a new code.",
                )
            if row.expires_at <= now:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="This code has expired. Request a new code.",
                )

            if not hmac.compare_digest(row.code_hash, _code_hash(account.id, body.code.strip())):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Incorrect code. Please check your email and try again.",
                )

            row.consumed_at = now
            try:
                db.commit()
            except SQLAlchemyError as exc:
                db.rollback()
                logger.error("VERIFY CODE — commit failed: %s", exc)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Server unavailable, please retry in a moment.",
                )

            token = jwt.encode(
                {
                    "sub": str(account.id),
                    "tenant": schema,
                    "purpose": RESET_PURPOSE,
                    "pw": _password_fingerprint(account.password_hash),
                    "exp": now + RESET_TOKEN_TTL,
                },
                SECRET_KEY,
                algorithm=ALGORITHM,
            )
    except OperationalError as exc:
        logger.error("VERIFY CODE — DB error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server unavailable, please retry in a moment.",
        )

    return {"reset_token": token, "expires_in_seconds": int(RESET_TOKEN_TTL.total_seconds())}


@router.post("/reset", summary="Set a new password")
def reset_password(body: ResetRequest, background_tasks: BackgroundTasks):
    """Step 3 — sets a new password using the reset token from /verify.

    How it works:
    - Decodes the JWT and reads the tenant schema name and password fingerprint.
    - Opens the tenant session, finds the employee, verifies the fingerprint still
      matches (if the password already changed, the fingerprint won't match and the
      token is rejected — single-use guarantee).
    - Hashes the new password with argon2 and saves it.
    - Sends a "your password was changed" notice in the background.

    Errors:
      400 — token expired, already used, or new password same as current
      503 — database error saving the new password
    """
    invalid = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="This reset link has expired or was already used. Start again.",
    )
    try:
        claims = jwt.decode(body.reset_token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise invalid

    if claims.get("purpose") != RESET_PURPOSE:
        raise invalid

    tenant_schema = claims.get("tenant", "")
    if not tenant_schema:
        raise invalid
    try:
        _validate_schema_name(tenant_schema)
    except ValueError:
        raise invalid

    try:
        with get_session_for_schema(tenant_schema) as db:
            employee = db.query(Employee).filter(
                Employee.id == int(claims.get("sub", 0))
            ).first()

            if employee is None or employee.deleted_at is not None or not employee.is_active:
                raise invalid

            # Fingerprint changes as soon as password changes — single-use guarantee.
            if claims.get("pw") != _password_fingerprint(employee.password_hash):
                raise invalid

            if verify_password(body.new_password, employee.password_hash):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Your new password must be different from your current password.",
                )

            employee.password_hash = pwd_context.hash(body.new_password)
            try:
                db.commit()
            except SQLAlchemyError:
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Could not save your new password. Please try again in a moment.",
                )

            background_tasks.add_task(send_password_changed_notice, employee.name, employee.email)

    except OperationalError as exc:
        logger.error("RESET PASSWORD — DB error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server unavailable, please retry in a moment.",
        )
    except ValueError:
        raise invalid

    return {"message": "Password updated. You can now log in with your new password."}
