"""
ContactFieldDefinition model — admin-editable field definitions for contact profiles.

WHAT IS THIS?
    Consumer and partner contacts each have a "profile" with extra fields beyond the
    standard contact data. For ventino, consumers have: house_stage, tank_type, tank_age.
    For a future electrical tenant, consumers might have: connection_type, load_kw.

    This table stores WHAT fields exist and WHAT their rules are (type, label, options).
    The actual values are stored in JSONB in consumer_profiles.data / partner_profiles.data.

HOW IT WORKS:
    1. At onboarding, the admin seeds this table with the fields relevant to their business.
    2. The frontend calls GET /contact-field-definitions?entity_type=consumer to know
       which fields to render and what dropdown options to show.
    3. When saving a profile, the backend validates each key in the JSONB data against
       these definitions (required check, dropdown option validation, type check).

WHY THIS APPROACH?
    - Fully tenant-customizable: each tenant schema has its own rows in this table.
    - Admin can add, rename, or reorder fields without any code or migration.
    - The frontend dynamically renders the form from these definitions.
    - field_type tells the frontend: 'dropdown' → <select>, 'number' → <input type=number>,
      'text' → <input type=text>, 'boolean' → <checkbox>.

MULTI-TENANT:
    Lives in each tenant's PostgreSQL schema. Same Python class, different data per tenant.
"""

import datetime
from sqlalchemy import BigInteger, Boolean, Integer, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from database import Base


class ContactFieldDefinition(Base):
    __tablename__ = "contact_field_definitions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 'consumer' or 'partner' — which profile type this field belongs to
    entity_type: Mapped[str] = mapped_column(String(10))
    # Matches the key stored in the JSONB data bag (e.g. 'tank_type', 'house_stage')
    field_name: Mapped[str] = mapped_column(String(50))
    # Human-readable label shown in the UI (e.g. 'Tank Type')
    field_label: Mapped[str | None] = mapped_column(String(100))
    # Controls how the frontend renders the field:
    #   'text'     → free-text input
    #   'dropdown' → select/dropdown from the options list
    #   'number'   → numeric input
    #   'boolean'  → checkbox
    field_type: Mapped[str] = mapped_column(String(20))
    # Non-null only for field_type='dropdown'. Example: ["plastic", "steel", "fiber"]
    options: Mapped[list | None] = mapped_column(JSONB)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
