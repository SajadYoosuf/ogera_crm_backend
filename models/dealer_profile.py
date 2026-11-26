from sqlalchemy import BigInteger, ForeignKey, Numeric, String, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from database import Base


class DealerProfile(Base):
    __tablename__ = "dealer_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("contacts.id"), unique=True, nullable=False)
    business_name: Mapped[str | None] = mapped_column(String(120))
    gstin: Mapped[str | None] = mapped_column(String(20))
    town_city: Mapped[str | None] = mapped_column(String(80))
    pin_code: Mapped[str | None] = mapped_column(String(10))
    dealer_type: Mapped[str | None] = mapped_column(String(60))
    assigned_rep: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id"))
    credit_limit: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    outstanding_balance: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (Index("idx_dealer_profiles_contact_id", "contact_id"),)
