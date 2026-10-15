import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class EmployeePresence(Base):
    """When an employee last used the app (a heartbeat from the client). Separate from the account's
    is_active flag, which an admin sets, and from the login token, which expires on its own schedule."""

    __tablename__ = "employee_presence"

    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True
    )
    # UTC, naive, like the rest of the backend.
    last_seen_at: Mapped[datetime.datetime] = mapped_column(DateTime)
