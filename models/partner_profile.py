from sqlalchemy import BigInteger, ForeignKey, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class PartnerProfile(Base):
    __tablename__ = "partner_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("contacts.id", ondelete="CASCADE"), unique=True
    )
    # Three-state: None=not assessed, 1=needs education (aware), 0=sales-ready
    awareness_mode: Mapped[int | None] = mapped_column(SmallInteger)
    # VARCHAR so new professions don't require a migration
    profession_type: Mapped[str] = mapped_column(String(80))
    work_area: Mapped[str | None] = mapped_column(String(120))
    sample_given: Mapped[int | None] = mapped_column(SmallInteger)      # 1=yes, 0=no
    projects_per_year: Mapped[int | None] = mapped_column(SmallInteger)
    # referrals_sent is derived: SELECT COUNT(*) FROM contacts WHERE referred_by_id = ?

    contact: Mapped["Contact"] = relationship("Contact", back_populates="partner_profile")
