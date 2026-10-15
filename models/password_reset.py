import datetime
from sqlalchemy import BigInteger, ForeignKey, SmallInteger, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class PasswordResetCode(Base):
    """One emailed 6-digit code. Only an HMAC of the code is stored, never the code itself."""

    __tablename__ = "password_reset_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Same type as employees.id (BIGINT), or MySQL refuses the foreign key.
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id"))
    code_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    # Set when the code is used, replaced by a newer code, or locked out after too many wrong tries.
    consumed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
