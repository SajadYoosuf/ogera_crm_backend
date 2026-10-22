from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class PartnerProfile(Base):
    __tablename__ = "partner_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("contacts.id", ondelete="CASCADE"), unique=True
    )
    # VARCHAR so new professions don't require a migration
    profession_type: Mapped[str] = mapped_column(String(80))
    work_area: Mapped[str | None] = mapped_column(String(120))
    # referrals_sent is derived: SELECT COUNT(*) FROM contacts WHERE referred_by_id = ?
    # NOTE: sample_given and projects_per_year columns still exist in the DB but are no
    # longer used. They can be dropped with: ALTER TABLE partner_profiles DROP COLUMN sample_given, DROP COLUMN projects_per_year;

    contact: Mapped["Contact"] = relationship("Contact", back_populates="partner_profile")
