"""Forgot password: email a one-time code to the employee's own address, check it, then set a new password.

Flow: POST /request (emails the code) -> POST /verify (returns a short-lived reset token)
-> POST /reset (sets the password). The reset token is tied to the current password hash, so it
stops working as soon as the password changes.
"""
import datetime
import hashlib
import hmac
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth import pwd_context, verify_password
from config import (
    ALGORITHM,
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    RESET_CODE_MAX_ATTEMPTS,
    RESET_CODE_RESEND_SECONDS,
    RESET_CODE_TTL_MINUTES,
    RESET_TOKEN_TTL_MINUTES,
    SECRET_KEY,
)
from database import get_db
from mailer import send_password_changed_notice, send_password_reset_code
from models import Employee, PasswordResetCode

router = APIRouter(prefix="/auth/password-reset", tags=["auth"])

# Values come from config.py (and .env), so they can be changed in one place.
CODE_TTL = datetime.timedelta(minutes=RESET_CODE_TTL_MINUTES)
RESEND_COOLDOWN = datetime.timedelta(seconds=RESET_CODE_RESEND_SECONDS)
MAX_ATTEMPTS = RESET_CODE_MAX_ATTEMPTS
RESET_TOKEN_TTL = datetime.timedelta(minutes=RESET_TOKEN_TTL_MINUTES)
RESET_PURPOSE = "password_reset"


class EmailRequest(BaseModel):
    email: EmailStr


class VerifyRequest(BaseModel):
    email: EmailStr
    code: str


class ResetRequest(BaseModel):
    reset_token: str
    new_password: str


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


def _account_for_email(db: Session, email: str) -> Employee:
    """The active employee with this email (ignoring case). 404 if there is none."""
    account = (
        db.query(Employee)
        .filter(
            func.lower(Employee.email) == email.strip().lower(),
            Employee.is_active == 1,
            Employee.deleted_at.is_(None),
        )
        .first()
    )
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active account uses this email.")
    return account


def _code_hash(employee_id: int, code: str) -> str:
    # Keyed with the app secret, so a leaked table cannot be brute-forced without the secret.
    return hmac.new(SECRET_KEY.encode(), f"{employee_id}:{code}".encode(), hashlib.sha256).hexdigest()


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if len(local) <= 2:
        return f"{local[:1]}***@{domain}"
    return f"{local[0]}***{local[-1]}@{domain}"


def _password_fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def _latest_code(db: Session, employee_id: int) -> PasswordResetCode | None:
    return (
        db.query(PasswordResetCode)
        .filter(PasswordResetCode.employee_id == employee_id)
        .order_by(PasswordResetCode.created_at.desc(), PasswordResetCode.id.desc())
        .first()
    )


def _active_code(db: Session, employee_id: int) -> PasswordResetCode | None:
    return (
        db.query(PasswordResetCode)
        .filter(PasswordResetCode.employee_id == employee_id, PasswordResetCode.consumed_at.is_(None))
        .order_by(PasswordResetCode.id.desc())
        .first()
    )


@router.post("/request", summary="Send a reset code")
def request_code(body: EmailRequest, db: Session = Depends(get_db)):
    """Emails a new 6-digit code to the address of the employee's account. Any active employee can use this.
    A code expires after 10 minutes, and requests are limited by a short cooldown.
    Sending a new code stops older codes from working. Returns 404 if no active account uses the email.
    """
    account = _account_for_email(db, body.email)
    now = _now()

    latest = _latest_code(db, account.id)
    if latest is not None and latest.created_at + RESEND_COOLDOWN > now:
        wait = int((latest.created_at + RESEND_COOLDOWN - now).total_seconds()) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {wait} seconds before requesting another code.",
        )

    db.query(PasswordResetCode).filter(
        PasswordResetCode.employee_id == account.id, PasswordResetCode.consumed_at.is_(None)
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

    result = send_password_reset_code(account.name, account.email, code, int(CODE_TTL.total_seconds() // 60))
    if not result["sent"]:
        db.rollback()  # no code is left behind that the user cannot receive
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=result["message"])

    db.commit()
    return {
        "email_masked": _mask_email(account.email),
        "expires_in_seconds": int(CODE_TTL.total_seconds()),
        "resend_in_seconds": int(RESEND_COOLDOWN.total_seconds()),
    }


@router.post("/verify", summary="Check the reset code")
def verify_code(body: VerifyRequest, db: Session = Depends(get_db)):
    """Checks the 6-digit code sent to the account's email. Five wrong attempts lock the code, and it must be requested again.
    On success returns a reset token for /auth/password-reset/reset, valid for 10 minutes.
    """
    account = _account_for_email(db, body.email)
    row = _active_code(db, account.id)
    now = _now()

    if row is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active code. Request a new code.")
    if row.expires_at <= now:
        row.consumed_at = now
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This code has expired. Request a new code.")
    if row.attempts >= MAX_ATTEMPTS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Too many wrong attempts. Request a new code.")

    if not hmac.compare_digest(row.code_hash, _code_hash(account.id, body.code.strip())):
        row.attempts += 1
        left = MAX_ATTEMPTS - row.attempts
        if left <= 0:
            row.consumed_at = now
        db.commit()
        if left <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Too many wrong attempts. Request a new code.")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Incorrect code. {left} attempts left.")

    row.consumed_at = now
    db.commit()
    token = jwt.encode(
        {
            "sub": str(account.id),
            "purpose": RESET_PURPOSE,
            "pw": _password_fingerprint(account.password_hash),
            "exp": now + RESET_TOKEN_TTL,
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    return {"reset_token": token, "expires_in_seconds": int(RESET_TOKEN_TTL.total_seconds())}


@router.post("/reset", summary="Set a new password")
def reset_password(body: ResetRequest, db: Session = Depends(get_db)):
    """Sets a new password using the reset token from /verify. The token works only once.
    The new password must be 6 to 15 characters and different from the current one.
    The employee is emailed to confirm the change.
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

    employee = db.query(Employee).filter(Employee.id == int(claims.get("sub", 0))).first()
    if employee is None or employee.deleted_at is not None or not employee.is_active:
        raise invalid
    # The password changed since the token was issued, so this token was already used.
    if claims.get("pw") != _password_fingerprint(employee.password_hash):
        raise invalid

    password = body.new_password
    if len(password) < PASSWORD_MIN_LENGTH:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Use at least {PASSWORD_MIN_LENGTH} characters.")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Use at most {PASSWORD_MAX_LENGTH} characters.")
    if verify_password(password, employee.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your new password must be different from your current password.",
        )

    # Hash the full password: login verifies the whole string, so nothing may be truncated here.
    employee.password_hash = pwd_context.hash(password)
    db.commit()
    # Best effort: the password is already changed, so a failed email is only logged.
    send_password_changed_notice(employee.name, employee.email)
    return {"message": "Password updated. You can now log in with your new password."}
