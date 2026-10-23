"""
Staff expense record for the field expense management module.

WHY THIS TABLE:
    Field staff submit receipts after each field visit. A manager or admin then
    approves or rejects each claim.

    IMPORTANT: The amount is write-once. Once an expense is created its amount
    cannot change. This protects against tampering after approval is given.

COLUMNS:
    employee_id       — who submitted this expense
    title             — short description e.g. "Fuel to Kottakkal site"
    category          — petrol / food / maintenance / accommodation / other
    amount            — decimal(10,2); set on creation and NEVER updated afterwards
    bill_photo_url    — optional path to the uploaded receipt image (served from /uploads)
    status            — pending → approved or rejected
    rejection_reason  — filled in only when status = rejected
    reviewed_by       — which manager / admin acted on it
    reviewed_at       — when the review happened
    deleted_at        — soft delete; allowed only by the creator while status = pending
"""
import datetime
from decimal import Decimal
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base

# Allowed values — the route validates against these at the API boundary.
EXPENSE_CATEGORIES = ("petrol", "food", "maintenance", "accommodation", "other")
EXPENSE_STATUSES = ("pending", "approved", "rejected")


class Expense(Base):
    __tablename__ = "expenses"
    __table_args__ = (
        Index("idx_expenses_employee_created", "employee_id", "created_at"),
        Index("idx_expenses_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)  # one of EXPENSE_CATEGORIES
    # Stored as Numeric to avoid floating-point rounding on money values.
    # Never update this column after insert — see the docstring above.
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    bill_photo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(15), default="pending")  # one of EXPENSE_STATUSES
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id"), nullable=True)
    reviewed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    employee: Mapped["Employee"] = relationship("Employee", foreign_keys=[employee_id])
    reviewer: Mapped["Employee | None"] = relationship("Employee", foreign_keys=[reviewed_by])
