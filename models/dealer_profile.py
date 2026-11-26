from sqlalchemy import BigInteger, ForeignKey, Numeric, String, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB
from database import Base


class DealerProfile(Base):
    """
    Dealer details for a contact of type 'dealer'.

    Dealer-specific questions are defined by the admin in Settings (contact_field_definitions,
    entity_type='dealer') and stored in `data`, the same way consumer and partner answers are.
    The typed columns (business_name, gstin, ...) are from migration 001 and are not written by the
    contact form yet.
    """
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

    contact: Mapped["Contact"] = relationship("Contact", back_populates="dealer_profile")
    rep: Mapped["Employee | None"] = relationship("Employee", foreign_keys=[assigned_rep])

    @property
    def assigned_rep_name(self) -> str | None:
        """Name of the employee responsible for this dealer, for the response schema."""
        return self.rep.name if self.rep else None

    __table_args__ = (Index("idx_dealer_profiles_contact_id", "contact_id"),)
