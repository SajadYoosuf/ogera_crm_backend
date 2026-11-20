"""
PartnerProfile schemas — request and response shapes for the partner profile.

Same JSONB pattern as ConsumerProfile. See schemas/consumer_profile.py for full explanation.

For ventino: data = {"partner_type": "plumber", "work_area": "malappuram"}
"""

from typing import Any
from pydantic import BaseModel, ConfigDict


class PartnerProfileBase(BaseModel):
    data: dict[str, Any] = {}


class PartnerProfileCreate(PartnerProfileBase):
    contact_id: int


class PartnerProfileUpdate(BaseModel):
    data: dict[str, Any] | None = None


class PartnerProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int
    data: dict[str, Any] = {}
