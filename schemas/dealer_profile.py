"""
DealerProfile schemas — request and response shapes for the dealer profile.

The dealer fields are fixed columns on dealer_profiles (see models/dealer_profile.py).
`data` holds any extra answers an admin defines in Settings (contact_field_definitions, entity_type='dealer').

Who may write what is enforced in routes/contacts.py: only admins may set credit_limit or outstanding_balance.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DealerProfileBase(BaseModel):
    business_name: str | None = Field(default=None, max_length=120)
    gstin: str | None = Field(default=None, max_length=20)
    dealer_type: str | None = Field(default=None, max_length=60)  # value from the 'dealer_type' dropdown
    town_city: str | None = Field(default=None, max_length=80)
    pin_code: str | None = Field(default=None, max_length=10)
    assigned_rep: int | None = None  # employee id of the rep responsible for this dealer
    credit_limit: float | None = Field(default=None, ge=0)        # admin only
    outstanding_balance: float | None = Field(default=None, ge=0)  # admin only
    data: dict[str, Any] = {}


class DealerProfileUpdate(DealerProfileBase):
    pass


class DealerProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int
    business_name: str | None = None
    gstin: str | None = None
    dealer_type: str | None = None
    town_city: str | None = None
    pin_code: str | None = None
    assigned_rep: int | None = None
    assigned_rep_name: str | None = None
    credit_limit: float | None = None
    outstanding_balance: float | None = None
    data: dict[str, Any] = {}
