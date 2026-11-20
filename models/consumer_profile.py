"""
ConsumerProfile model — extra profile data for consumer contacts, stored as JSONB.

WHAT IS THIS?
    When a contact is of type 'consumer', we ask extra questions like:
      - What is the current house stage?
      - What type of water tank do they have?
      - How old is the tank?

    For ventino these are specific questions. Another tenant might ask completely different
    questions. That's why answers are stored in a JSONB column called `data` instead of
    fixed columns like `house_stage VARCHAR(50)`.

HOW JSONB WORKS:
    JSONB is PostgreSQL's binary JSON type. It stores any key-value structure:
        data = {"house_stage": "occupied", "tank_type": "plastic", "tank_age": 3}
    You can read fields in Python: profile.data.get("tank_type")
    You can query in SQL:          ConsumerProfile.data["tank_type"].as_string()

WHY ONE DATA COLUMN INSTEAD OF FIXED COLUMNS:
    - Ventino: house_stage, tank_type, tank_age
    - Future electrical tenant: connection_type, panel_brand, load_kw
    With fixed columns, each new tenant type needs a migration + code change.
    With JSONB, the admin seeds contact_field_definitions at onboarding — zero code change.

VALIDATION:
    The backend reads contact_field_definitions for the tenant schema and validates
    each key in `data` before saving (required check, dropdown option check, type check).
    See services/contact_field_service.py for the validation logic.
"""

from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB
from database import Base


class ConsumerProfile(Base):
    __tablename__ = "consumer_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("contacts.id", ondelete="CASCADE"), unique=True
    )
    # All profile answers live here as a JSONB dict.
    # For ventino: {"house_stage": "occupied", "tank_type": "plastic", "tank_age": 3}
    # Defaults to empty dict (no answers filled in yet).
    data: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    contact: Mapped["Contact"] = relationship("Contact", back_populates="consumer_profile")
