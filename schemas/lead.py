"""
Lead API schemas — Pydantic request and response models.

source_id references lead_sources.id (1=Walk-in, 2=Referral, 3=Meta Ads,
4=Website, 5=WhatsApp, 6=JustDial, 7=GMB, 8=Field Visit).
source_name is denormalised from lead_sources.name for display only.
"""

import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from phone_utils import clean_phone


# ── Authenticated lead schemas ────────────────────────────────────────────────

class LeadCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=200)
    last_name: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None)
    email: str | None = Field(default=None)
    # source_id → FK to lead_sources.id; default Walk-in (1) if staff doesn't specify
    source_id: int | None = None
    notes: str | None = None
    address: str | None = None
    tags: list[str] | None = None
    assigned_to: int | None = None

    @field_validator("phone", mode="before")
    @classmethod
    def _clean(cls, v: str | None) -> str | None:
        if v is None or str(v).strip() == "":
            return None
        return clean_phone(str(v))


class LeadUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=200)
    last_name: str | None = Field(default=None, max_length=100)
    phone: str | None = None
    email: str | None = None
    status: Literal["new", "contacted", "qualified", "converted", "lost"] | None = None
    source_id: int | None = None
    notes: str | None = None
    address: str | None = None
    tags: list[str] | None = None
    assigned_to: int | None = None

    @field_validator("phone", mode="before")
    @classmethod
    def _clean(cls, v: str | None) -> str | None:
        if v is None or str(v).strip() == "":
            return None
        return clean_phone(str(v))


class LeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str | None
    last_name: str | None
    phone: str | None
    email: str | None
    source_id: int | None
    source_name: str | None = None
    status: str
    score: int
    notes: str | None
    address: str | None
    tags: list[str] | None
    raw_payload: dict | None
    whatsapp_sent: bool
    whatsapp_sent_at: datetime.datetime | None
    assigned_to: int | None
    created_by: int | None
    converted_at: datetime.datetime | None
    contact_id: int | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class LeadListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str | None
    last_name: str | None
    phone: str | None
    email: str | None
    source_id: int | None
    source_name: str | None = None
    status: str
    score: int
    whatsapp_sent: bool
    assigned_to: int | None
    assigned_name: str | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class LeadListResponse(BaseModel):
    items: list[LeadListItem]
    total: int
    skip: int
    limit: int


class ConvertLeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    lead_id: int
    contact_id: int | None   # None until contacts module is built
    created: bool


class LeadStatsResponse(BaseModel):
    by_status: dict[str, int]
    by_source: dict[str, int]
    total: int


# ── Public inbound schemas (no JWT, only API key) ─────────────────────────────

class WebsiteLeadCreate(BaseModel):
    model_config = ConfigDict(extra="allow")

    first_name: str = Field(min_length=1, max_length=200)
    last_name: str | None = Field(default=None, max_length=100)
    phone: str | None = None
    email: str | None = None
    notes: str | None = None

    @field_validator("phone", mode="before")
    @classmethod
    def _clean(cls, v: str | None) -> str | None:
        if v is None or str(v).strip() == "":
            return None
        return clean_phone(str(v))


class SheetLeadCreate(BaseModel):
    model_config = ConfigDict(extra="allow")

    first_name: str = Field(min_length=1, max_length=200)
    last_name: str | None = Field(default=None, max_length=100)
    phone: str | None = None
    email: str | None = None
    ad_name: str | None = None
    form_name: str | None = None
    campaign_id: str | None = None
    ad_set_id: str | None = None
    notes: str | None = None

    @field_validator("phone", mode="before")
    @classmethod
    def _clean(cls, v: str | None) -> str | None:
        if v is None or str(v).strip() == "":
            return None
        try:
            return clean_phone(str(v))
        except ValueError:
            return None


class InboundLeadResponse(BaseModel):
    id: int
    status: str = "created"


# ── Tenant API key management schemas ─────────────────────────────────────────

class TenantApiKeyCreate(BaseModel):
    label: str = Field(default="default", min_length=1, max_length=60)


class TenantApiKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    label: str
    is_active: bool
    created_at: datetime.datetime


class TenantApiKeyCreatedResponse(TenantApiKeyResponse):
    raw_key: str
