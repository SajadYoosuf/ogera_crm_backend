"""
Contact schemas — the "contract" between the API and its callers.

WHAT IS THIS FILE?
------------------
Pydantic schemas define the shape of data coming IN to (requests) and going OUT
of (responses) the API. They are separate from the SQLAlchemy models, which only
care about the database table structure.

WHY KEEP MODELS AND SCHEMAS SEPARATE?
--------------------------------------
The database model has fields like `phone_key` (internal duplicate-check helper)
and `deleted_at` (soft-delete timestamp) that callers should never see or set.
Schemas let us expose exactly what we choose — nothing more.

SCHEMA HIERARCHY (inheritance chain):
    ContactBase          ← shared fields for both create and response
        └── ContactCreate    ← fields accepted when creating a new contact (POST)
    ContactUpdate        ← all fields optional; only sent fields are changed (PUT)
    ContactResponse      ← full contact detail returned after create / GET by id
    ContactListItem      ← slim row for the paginated list (GET /contacts)
    ContactListResponse  ← wraps a list of ContactListItems with pagination info

VALIDATION:
    The `_clean_phone` validator runs before any phone value is stored.
    It normalises the number (strips spaces, dashes, country code) so the DB
    always holds a consistent format. See `phone_utils.clean_phone` for details.
"""

import datetime
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field
from .enums import ContactType
from .employee import EmployeeSummary
from .lead_source import LeadSourceSummary
from .lead_stage import LeadStageResponse
from .consumer_profile import (
    ConsumerProfileBase,
    ConsumerProfileResponse,
    ConsumerProfileUpdate,
)
from .partner_profile import PartnerProfileBase, PartnerProfileResponse, PartnerProfileUpdate
from .dealer_profile import DealerProfileBase, DealerProfileResponse, DealerProfileUpdate
from phone_utils import clean_phone
from pydantic import field_validator


class ProductRef(BaseModel):
    """
    Minimal product snapshot embedded inside a contact's response.

    WHY A SEPARATE CLASS?
        ContactResponse embeds the products a contact is interested in.
        We only need id and name here — returning full pricing and specs would
        make the contact detail payload unnecessarily large.

    from_attributes=True lets Pydantic read directly from a SQLAlchemy Product
    ORM object (contact.interested_products is a list of Product rows).
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class ContactSummary(BaseModel):
    """
    Shallow contact snapshot used only inside other responses.

    WHY THIS EXISTS:
        ContactResponse includes a `referred_by` field (who referred this contact).
        If that field were also a full ContactResponse, it would include its own
        `referred_by`, which could include another full ContactResponse, and so on
        forever (infinite recursion). ContactSummary breaks the cycle by returning
        only the minimum fields needed to display the referrer's name and phone.
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str | None = None
    phone: str
    contact_type: ContactType
    district: str | None = None


class NameRef(BaseModel):
    """
    Minimal id + name pair used wherever we need to display a linked object's name
    without loading all of its fields (e.g. assigned_to employee in list rows).
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class LeadStageSummary(BaseModel):
    """
    The contact's current pipeline stage, embedded inside each list row.

    WHY A SUMMARY AND NOT THE FULL LeadStageResponse?
        The list endpoint returns many rows at once. Including every stage field
        for every row would send far more data than the UI needs. This slim version
        carries only what the Kanban board / list card requires: stage id, slug,
        name, and when it was last changed.
    """
    model_config = ConfigDict(from_attributes=True)

    stage_id: int
    stage_slug: str = ""
    stage_name: str = ""
    changed_at: datetime.datetime


class ContactBase(BaseModel):
    """
    Fields shared between ContactCreate and ContactResponse.

    Placing shared fields in a base class avoids duplication. Both the "create"
    and the "response" schemas inherit these fields automatically.

    PHONE VALIDATION:
        The `_clean_phone` validator runs automatically before Pydantic stores any
        phone value. It calls `clean_phone()` (see phone_utils.py), which strips
        formatting and normalises the number. This means callers can send
        "+91 98765-43210" and the DB will always store "9876543210".
    """
    contact_type: ContactType = ContactType.consumer
    # Dealers: the account (shop or business) name. Optional — phone is used as display name when absent.
    name: Annotated[str, Field(min_length=1, max_length=150)] | None = None
    phone: Annotated[str, Field(min_length=1, max_length=20)]
    phone_alt: Annotated[str, Field(max_length=20)] | None = None
    district: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    address: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    # Optional: contact that referred this one
    referred_by_id: int | None = None
    lead_source_id: int | None = None
    # Free-text note about the contact. Optional for every contact type.
    notes: str | None = None

    @field_validator("phone", "phone_alt", mode="before")
    @classmethod
    def _clean_phone(cls, value):
        """
        Normalise phone numbers before Pydantic stores them.

        `mode="before"` means this runs before the type/length checks, so the
        cleaned value is what gets validated. If clean_phone raises a ValueError
        (e.g. too short after stripping), Pydantic surfaces it as a 422 error.
        """
        if value is None:
            return value
        try:
            return clean_phone(value)
        except ValueError as exc:
            raise ValueError(str(exc))


# Reusable type aliases with built-in range validation.
# Annotated[float, Field(...)] means: a float, plus Pydantic constraints.
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


class ContactCreate(ContactBase):
    """
    Request body for POST /contacts.

    Inherits all ContactBase fields, then adds create-only fields:
        - lead_source_id  → required (overrides the optional base version)
        - assigned_to     → omit to leave unassigned; manager picks it up later
        - latitude/longitude → required from mobile for field_visit leads, forbidden for all others
        - *_profile       → type-specific details created in the same transaction
        - interested_product_ids → list of product ids this contact is interested in
    """
    # Overrides ContactBase.lead_source_id to make it required on create
    lead_source_id: int
    # Omit to create an unassigned contact; manager assigns after reviewing the new lead
    assigned_to: int | None = None
    # Required for Offline (field visit) leads, not allowed for other sources
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    # Type details, created in the same transaction. Each one is only allowed for its own contact type.
    consumer_profile: ConsumerProfileBase | None = None
    partner_profile: PartnerProfileBase | None = None
    dealer_profile: DealerProfileBase | None = None
    # Products this contact is interested in. Empty list = no products linked.
    interested_product_ids: list[int] = []


class ContactUpdate(BaseModel):
    """
    Request body for PUT /contacts/{id}.

    Every field is optional — callers send only the fields they want to change.
    The route uses `body.model_fields_set` to know which fields were actually
    provided, so sending `null` is different from omitting the field entirely.

    PROFILE UPSERT BEHAVIOUR:
        If the contact has no profile yet and a profile block is provided, the
        route creates a new profile. If one already exists, it merges the changes
        (only the sent fields are updated).
    """
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

    # Creates the profile if the contact has none, otherwise merges the given fields
    consumer_profile: ConsumerProfileUpdate | None = None
    partner_profile: PartnerProfileUpdate | None = None
    dealer_profile: DealerProfileUpdate | None = None
    # Replaces the full list of interested products when provided.
    # Omit → keep existing. Send [] → clear all. Send [1, 2] → replace with those ids.
    interested_product_ids: list[int] | None = None
    # Free-text note. Omit → keep existing. Send null → clear. Send string → replace.
    notes: str | None = None

    @field_validator("phone", "phone_alt", mode="before")
    @classmethod
    def _clean_phone(cls, value):
        """Same normalisation as ContactBase — see ContactBase._clean_phone."""
        if value is None:
            return value
        try:
            return clean_phone(value)
        except ValueError as exc:
            raise ValueError(str(exc))


class ContactResponse(ContactBase):
    """
    Full contact detail, returned by GET /contacts/{id}, POST /contacts, and PUT /contacts/{id}.

    HOW TO POPULATE THIS FROM AN ORM OBJECT:
        response = ContactResponse.model_validate(contact, from_attributes=True)

        `from_attributes=True` tells Pydantic to read values from the SQLAlchemy
        object's attributes instead of a plain dict. This works because
        ConfigDict(from_attributes=True) is set below.

    FIELDS THAT ARE NOT POPULATED AUTOMATICALLY:
        `referrals_count` and `current_stage` require extra queries that Pydantic
        cannot run on its own. The route handler must set them explicitly after
        calling model_validate:

            response.referrals_count = db.query(func.count(Contact.id)) \\
                .filter(Contact.referred_by_id == contact.id).scalar() or 0
            response.current_stage = _stage_response(latest_stage)
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    address: str | None = None  # legacy rows may have no address
    assigned_to: int | None = None
    created_by: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    deleted_at: datetime.datetime | None = None

    # Nested objects populated from SQLAlchemy relationships
    assigned_employee: EmployeeSummary | None = None
    lead_source: LeadSourceSummary | None = None
    referred_by: ContactSummary | None = None

    # Must be set by the route after model_validate (not auto-populated)
    referrals_count: int = 0
    is_verified: bool = False
    missing_fields: list[str] = []
    current_stage: LeadStageResponse | None = None

    # Populated from the DB column (from_attributes=True handles this automatically)
    notes: str | None = None

    # Only one of these will have data depending on contact_type
    consumer_profile: ConsumerProfileResponse | None = None
    partner_profile: PartnerProfileResponse | None = None
    dealer_profile: DealerProfileResponse | None = None

    # Products this contact is interested in (populated from the M2M relationship)
    interested_products: list[ProductRef] = []

    # True when the creator is admin/manager — signals the web to show the "Schedule first follow-up?" prompt
    auto_followup_needed: bool = False


class ContactListItem(BaseModel):
    """
    Slim row for GET /contacts — one item in the paginated list.

    WHY NOT REUSE ContactResponse?
        ContactResponse is rich (includes full profile, referral chain, all timestamps).
        Loading all that data for every row in a list of 100 contacts would be slow
        and wasteful. This slim schema includes only what a list card or table row
        needs: identity, type-specific headline fields, current stage, and verification.

    TYPE-SPECIFIC FIELDS:
        Some fields only apply to one type and will be None for others:
            profession_type  → partner only
            house_stage      → consumer only
            owner_name       → dealer only
            work_area        → partner only
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_type: ContactType
    name: str | None = None
    phone: str
    address: str | None = None
    district: str | None = None
    profession_type: str | None = None  # partners only
    house_stage: str | None = None      # consumers only
    owner_name: str | None = None       # dealers only
    work_area: str | None = None        # partners only
    latitude: float | None = None
    longitude: float | None = None
    lead_stage: LeadStageSummary | None = None
    lead_source: LeadSourceSummary | None = None
    assigned_to: NameRef | None = None
    created_by: NameRef | None = None
    # Branch of the employee who created the contact (e.g. "malappuram"); None when unknown
    created_by_branch: str | None = None
    created_at: datetime.datetime
    # Computed on read: True when all required fields for the contact type are filled
    is_verified: bool = False
    missing_fields: list[str] = []


class PipelineStageItem(BaseModel):
    """One pipeline stage definition, used to build the Kanban board columns."""
    id: int        # pipeline_stages.id — use this as stage_id when moving a contact
    slug: str
    label: str
    order_index: int
    contact_type: str


class ContactListResponse(BaseModel):
    """
    Paginated list response wrapper.

    `total` is the count of ALL matching contacts (ignoring skip/limit), so the
    UI can show "Showing 1–10 of 243" and calculate how many pages there are.
    """
    items: list[ContactListItem]
    total: int
    skip: int
    limit: int
