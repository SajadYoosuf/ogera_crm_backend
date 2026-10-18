import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class ContactAssignment(Base):
    """One owner change on a contact. `assigned_from` is empty for the first owner."""

    __tablename__ = "contact_assignments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("contacts.id"))
    assigned_from: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id"), nullable=True)
    assigned_to: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id"))
    changed_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id"))
    changed_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    from_employee: Mapped["Employee | None"] = relationship("Employee", foreign_keys=[assigned_from])
    to_employee: Mapped["Employee"] = relationship("Employee", foreign_keys=[assigned_to])
    changer: Mapped["Employee"] = relationship("Employee", foreign_keys=[changed_by])

    __table_args__ = (Index("idx_contact_assignments_contact_id", "contact_id"),)
