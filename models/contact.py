import datetime
from sqlalchemy import BigInteger, Numeric, Integer, String, ForeignKey, Text, DateTime, Enum as SAEnum, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_type: Mapped[str] = mapped_column(
        SAEnum("consumer", "partner", "dealer"), default="consumer"
    )
    name: Mapped[str | None] = mapped_column(String(150))
    phone: Mapped[str] = mapped_column(String(20))
    # Last 10 digits of phone; the duplicate check compares this across every contact type
    phone_key: Mapped[str] = mapped_column(String(10))
    phone_alt: Mapped[str | None] = mapped_column(String(20))
    # Required from the mobile app only (see client_platform); the web form may leave it blank
    district: Mapped[str | None] = mapped_column(String(80))
    address: Mapped[str | None] = mapped_column(String(255))
    # Captured only for offline (field visit) leads
    latitude: Mapped[float | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[float | None] = mapped_column(Numeric(9, 6))
    assigned_to: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id"))
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id"))
    referred_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("contacts.id"))
    lead_source_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("lead_sources.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    assigned_employee: Mapped["Employee"] = relationship(
        "Employee", back_populates="contacts", foreign_keys=[assigned_to]
    )
    # Employee who entered the lead (required for offline leads)
    creator: Mapped["Employee | None"] = relationship("Employee", foreign_keys=[created_by])
    # Self-referential: contact who referred this one (many-to-one toward parent)
    referred_by: Mapped["Contact | None"] = relationship(
        "Contact", foreign_keys=[referred_by_id], remote_side=[id], back_populates="referrals"
    )
    # All contacts this contact has referred (one-to-many)
    referrals: Mapped[list["Contact"]] = relationship(
        "Contact", foreign_keys=[referred_by_id], back_populates="referred_by"
    )
    lead_source: Mapped["LeadSource | None"] = relationship("LeadSource", back_populates="contacts")
    lead_stages: Mapped[list["LeadStage"]] = relationship("LeadStage", back_populates="contact")
    consumer_profile: Mapped["ConsumerProfile | None"] = relationship(
        "ConsumerProfile", back_populates="contact", uselist=False
    )
    partner_profile: Mapped["PartnerProfile | None"] = relationship(
        "PartnerProfile", back_populates="contact", uselist=False
    )
    dealer_profile: Mapped["DealerProfile | None"] = relationship(
        "DealerProfile", back_populates="contact", uselist=False
    )

    __table_args__ = (
        Index("idx_contacts_phone_key", "phone_key"),
        Index("idx_contacts_type", "contact_type"),
        Index("idx_contacts_assigned", "assigned_to"),
        Index("idx_contacts_district", "district"),
        Index("idx_contacts_created_by", "created_by"),
    )
