import datetime
from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class Sale(Base):
    """A sale record, optionally linked to the CRM contact that produced it."""

    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("contacts.id"), index=True)
    created_by_employee_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id"), index=True)
    purchase_date: Mapped[datetime.date | None] = mapped_column(Date, index=True)
    name: Mapped[str] = mapped_column(String(150))
    phone: Mapped[str | None] = mapped_column(String(20), index=True)
    alt_phone: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(Text)
    # Free text: the product name as typed, e.g. "1000L plastic tank".
    purchased_product: Mapped[str | None] = mapped_column(String(150))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    contact: Mapped["Contact | None"] = relationship("Contact")
    created_by_employee: Mapped["Employee | None"] = relationship("Employee")

    @property
    def created_by(self):
        """Public response name for the employee who recorded the sale."""
        return self.created_by_employee
