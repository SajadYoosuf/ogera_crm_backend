"""
ConsumerProfile schemas — request and response shapes for the consumer profile.

WHAT IS A CONSUMER PROFILE?
    Extra questions answered for consumer-type contacts. For ventino: house stage, tank type,
    tank age. For another tenant: completely different fields.

    Answers are stored as a JSONB dict in consumer_profiles.data:
        {"house_stage": "occupied", "tank_type": "plastic", "tank_age": 3}

HOW VALIDATION WORKS:
    The schema itself only checks that `data` is a dict (any keys allowed).
    Field-level validation (required fields, dropdown options, type checking) happens
    at the service layer by reading contact_field_definitions from the DB.
    See services/contact_field_service.py for that logic.

WHY NOT TYPED FIELDS IN THE SCHEMA?
    If we defined `house_stage: str | None`, `tank_type: str | None` etc., these schemas
    would only work for ventino. The JSONB/dict approach works for every tenant.
"""

from typing import Any
from pydantic import BaseModel, ConfigDict


class ConsumerProfileBase(BaseModel):
    # All profile answers as a free-form dict.
    # Keys and values depend on the tenant's contact_field_definitions.
    # Ventino example: {"house_stage": "occupied", "tank_type": "plastic", "tank_age": 3}
    data: dict[str, Any] = {}


class ConsumerProfileCreate(ConsumerProfileBase):
    contact_id: int


class ConsumerProfileUpdate(BaseModel):
    # None means "don't touch profile"; {} means "clear all answers"
    data: dict[str, Any] | None = None


class ConsumerProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int
    data: dict[str, Any] = {}
