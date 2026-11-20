"""
PartnerProfile model — extra profile data for partner contacts, stored as JSONB.

WHAT IS THIS?
    When a contact is of type 'partner' (a tradesperson who refers customers), we ask:
      - What type of partner are they? (plumber, contractor, builder...)
      - What area do they work in?

    Same JSONB pattern as ConsumerProfile: answers stored as a flexible dict so the
    admin can define different questions per tenant without any code change.

HOW IT WORKS:
    For ventino: data = {"partner_type": "plumber", "work_area": "malappuram"}
    Another tenant: data = {"partner_type": "electrician", "license_no": "EL-123"}

See models/consumer_profile.py and services/contact_field_service.py for more detail.
"""

from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB
from database import Base


class PartnerProfile(Base):
    __tablename__ = "partner_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("contacts.id", ondelete="CASCADE"), unique=True
    )
    # All profile answers live here as a JSONB dict.
    # For ventino: {"partner_type": "plumber", "work_area": "malappuram"}
    data: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    contact: Mapped["Contact"] = relationship("Contact", back_populates="partner_profile")
