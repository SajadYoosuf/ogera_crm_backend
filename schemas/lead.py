"""
Lead API schemas — Pydantic request and response models.

source_id references lead_sources.id (1=Walk-in, 2=Referral, 3=Meta Ads,
4=Website, 5=WhatsApp, 6=JustDial, 7=GMB, 8=Field Visit).
source_name is denormalised from lead_sources.name for display only.
"""

import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from phone_utils import clean_phone


def _normalise_email(v: str | None) -> str | None:
    """Strip, lowercase, and basic-validate an email. Raises ValueError for clearly invalid input."""
    if v is None or str(v).strip() == "":
        return None
    v = str(v).strip().lower()
    if "@" not in v or "." not in v.split("@", 1)[-1]:
        raise ValueError("Enter a valid email address")
    return v


# ── Authenticated lead schemas ────────────────────────────────────────────────

class LeadCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=200)
    last_name: str | None = Field(default=None, max_length=100)
    # Required — a lead with no phone number cannot be followed up or WhatsApp'd.
    # Send the full number with country code, e.g. "+919876543210".
    phone: str = Field(min_length=1, max_length=30)
    # source_id → FK to lead_sources.id; default Walk-in (1) if staff doesn't specify
    source_id: int | None = None
    notes: str | None = Field(default=None, max_length=5000)
    address: str | None = Field(default=None, max_length=500)
    tags: list[str] | None = None
    assigned_to: int | None = None
    # branch_id → FK to branches.id. Omit to default to the creating employee's own
    # branch (set by the route); send a different id to pick another branch.
    branch_id: int | None = None
    # created_at: optional override for imports (historical leads from sheets / Meta Ads)
    created_at: datetime.datetime | None = None

    @field_validator("phone", mode="before")
    @classmethod
    def _clean(cls, v: str) -> str:
        return clean_phone(str(v))


class LeadUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=200)
    last_name: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=254)
    # Validated against the active lead_statuses table (see services.lead_service
    # .update_lead), not hardcoded here — "converted" is also accepted since it's
    # the terminal value POST /leads/{id}/convert sets.
    status: str | None = Field(default=None, max_length=30)
    source_id: int | None = None
    notes: str | None = Field(default=None, max_length=5000)
    address: str | None = Field(default=None, max_length=500)
    tags: list[str] | None = None
    assigned_to: int | None = None
    branch_id: int | None = None

    @field_validator("phone", mode="before")
    @classmethod
    def _clean(cls, v: str | None) -> str | None:
        if v is None or str(v).strip() == "":
            return None
        return clean_phone(str(v))

    @field_validator("email", mode="before")
    @classmethod
    def _validate_email(cls, v: str | None) -> str | None:
        return _normalise_email(v)


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
    # Filled by get_lead_detail (GET /leads/{id}); create/update responses leave it None.
    assigned_name: str | None = None
    created_by: int | None
    created_by_name: str | None = None
    branch_id: int | None = None
    branch_name: str | None = None
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
    tags: list[str] | None = None
    whatsapp_sent: bool
    assigned_to: int | None
    assigned_name: str | None = None
    created_by: int | None = None
    created_by_name: str | None = None
    branch_id: int | None = None
    branch_name: str | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class LeadListResponse(BaseModel):
    items: list[LeadListItem]
    total: int
    skip: int
    limit: int


class LeadStatusOption(BaseModel):
    """One row from the lead_statuses table, as returned by GET /leads/statuses.

    The frontend status dropdown (table + detail modal) fetches this list instead
    of hardcoding it, so adding/renaming/reordering a status is a DB change only —
    no frontend deploy needed.
    """
    model_config = ConfigDict(from_attributes=True)

    value: str = Field(validation_alias="slug", serialization_alias="value")
    label: str = Field(validation_alias="name", serialization_alias="label")


class ConvertLeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    lead_id: int
    contact_id: int | None   # None until contacts module is built
    created: bool


class LeadStatsResponse(BaseModel):
    by_status: dict[str, int]
    by_source: dict[str, int]
    total: int
    # unassigned_by_source: source_name → count of unassigned leads for that source
    unassigned_by_source: dict[str, int] = Field(default_factory=dict)


# ── Bulk-assign schemas ───────────────────────────────────────────────────────

class BulkAssignRule(BaseModel):
    """
    One assignment rule: assign `count` unassigned leads from `source_id`
    to employee `assigned_to`.  source_id=None means "any source".
    """
    source_id: int | None = None
    assigned_to: int
    count: int = Field(gt=0, le=500)


class BulkAssignRequest(BaseModel):
    assignments: list[BulkAssignRule] = Field(min_length=1, max_length=50)


class BulkAssignResponse(BaseModel):
    assigned: int


class BulkDeleteLeadsRequest(BaseModel):
    """Same filters as GET /leads. At least one must be set, so nothing is deleted by accident."""
    status: str | None = None
    source_id: int | None = None
    assigned_to: int | None = None
    date_from: datetime.date | None = None
    date_to: datetime.date | None = None
    search: str | None = None


class BulkDeleteLeadsResponse(BaseModel):
    deleted: int


# ── Public inbound schemas (no JWT, only API key) ─────────────────────────────

class WebsiteLeadCreate(BaseModel):
    model_config = ConfigDict(extra="allow")

    first_name: str = Field(min_length=1, max_length=200)
    last_name: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=254)
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("phone", mode="before")
    @classmethod
    def _clean(cls, v: str | None) -> str | None:
        if v is None or str(v).strip() == "":
            return None
        return clean_phone(str(v))

    @field_validator("email", mode="before")
    @classmethod
    def _validate_email(cls, v: str | None) -> str | None:
        return _normalise_email(v)


class SheetLeadCreate(BaseModel):
    model_config = ConfigDict(extra="allow")

    first_name: str = Field(min_length=1, max_length=200)
    last_name: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=254)
    ad_name: str | None = Field(default=None, max_length=500)
    form_name: str | None = Field(default=None, max_length=200)
    campaign_id: str | None = Field(default=None, max_length=100)
    ad_set_id: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("phone", mode="before")
    @classmethod
    def _clean(cls, v: str | None) -> str | None:
        if v is None or str(v).strip() == "":
            return None
        try:
            return clean_phone(str(v))
        except ValueError:
            return None

    @field_validator("email", mode="before")
    @classmethod
    def _validate_email(cls, v: str | None) -> str | None:
        return _normalise_email(v)


class InboundLeadResponse(BaseModel):
    id: int
    status: str = "created"


class WhatsAppSendResponse(BaseModel):
    success: bool


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
