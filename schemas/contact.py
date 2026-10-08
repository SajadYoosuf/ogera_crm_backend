import datetime
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field
from .enums import ContactType, ProfessionType, StageEnum
from .employee import EmployeeSummary
from .lead_source import LeadSourceSummary
from .lead_stage import LeadStageResponse
from .consumer_profile import (
    ConsumerProfileBase,
    ConsumerProfileResponse,
    ConsumerProfileUpdate,
)
from .partner_profile import PartnerProfileBase, PartnerProfileResponse, PartnerProfileUpdate


class ContactSummary(BaseModel):
    """Shallow contact snapshot — used for referred_by to avoid infinite recursion."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone: str
    contact_type: ContactType
    district: str


class NameRef(BaseModel):
    """Minimal id + name reference for list rows."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class LeadStageSummary(BaseModel):
    """Current stage snapshot for list rows."""
    model_config = ConfigDict(from_attributes=True)

    stage: StageEnum
    changed_at: datetime.datetime


class ContactBase(BaseModel):
    contact_type: ContactType = ContactType.consumer
    name: Annotated[str, Field(min_length=1, max_length=150)]
    phone: Annotated[str, Field(min_length=1, max_length=20)]
    phone_alt: Annotated[str, Field(max_length=20)] | None = None
    district: Annotated[str, Field(min_length=1, max_length=80)]
    address: Annotated[str, Field(min_length=1, max_length=255)]
    # Optional: contact that referred this one
    referred_by_id: int | None = None
    lead_source_id: int | None = None


Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


class ContactCreate(ContactBase):
    # Omit to auto-assign a random eligible employee (not admin / manager / support)
    assigned_to: int | None = None
    # Required for Offline (field visit) leads, not allowed for other sources
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    # Optional consumer details, created in the same transaction (consumer contacts only)
    consumer_profile: ConsumerProfileBase | None = None
    # Optional partner details (partner contacts only); profession_type picks from ProfessionType
    partner_profile: PartnerProfileBase | None = None


class ContactUpdate(BaseModel):
    contact_type: ContactType | None = None
    name: Annotated[str, Field(min_length=1, max_length=150)] | None = None
    phone: Annotated[str, Field(min_length=1, max_length=20)] | None = None
    phone_alt: Annotated[str, Field(max_length=20)] | None = None
    district: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    address: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    assigned_to: int | None = None
    referred_by_id: int | None = None
    lead_source_id: int | None = None
    # Creates the profile if the contact has none, otherwise updates the given fields
    consumer_profile: ConsumerProfileUpdate | None = None
    # Same for partners; a profile created here must include profession_type
    partner_profile: PartnerProfileUpdate | None = None


class ContactResponse(ContactBase):
    """
    ORM-backed contact response.

    Populated via `ContactResponse.model_validate(contact, from_attributes=True)`.
    `referrals_count` and `current_stage` are NOT populated automatically — the route/service
    layer must set them explicitly after model_validate (Pydantic v2 models are mutable):

        response = ContactResponse.model_validate(contact, from_attributes=True)
        response.referrals_count = db.query(func.count(Contact.id)) \\
            .filter(Contact.referred_by_id == contact.id).scalar() or 0
        response.current_stage = ContactStageResponse.model_validate(
            max(contact.stages, key=lambda s: s.changed_at), from_attributes=True
        ) if contact.stages else None
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    address: str | None = None  # legacy rows may have no address
    assigned_to: int
    created_by: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    deleted_at: datetime.datetime | None = None

    assigned_employee: EmployeeSummary | None = None
    lead_source: LeadSourceSummary | None = None
    referred_by: ContactSummary | None = None
    referrals_count: int = 0
    is_verified: bool = False
    missing_fields: list[str] = []
    current_stage: LeadStageResponse | None = None
    consumer_profile: ConsumerProfileResponse | None = None
    partner_profile: PartnerProfileResponse | None = None


class ContactListItem(BaseModel):
    """Slim row for GET /contacts — works for every contact_type."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_type: ContactType
    name: str
    phone: str
    address: str | None = None
    district: str
    profession_type: ProfessionType | None = None  # partners only
    lead_stage: LeadStageSummary | None = None
    lead_source: NameRef | None = None
    assigned_to: NameRef
    created_by: NameRef | None = None
    created_at: datetime.datetime
    # Computed on read: verified once no required detail is pending
    is_verified: bool = False
    missing_fields: list[str] = []


class ContactListResponse(BaseModel):
    items: list[ContactListItem]
    total: int
    skip: int
    limit: int
