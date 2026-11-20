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
from .branch import BranchResponse
from .employee import EmployeeSummary
from .lead_source import LeadSourceSummary
from .consumer_profile import (
    ConsumerProfileBase,
    ConsumerProfileResponse,
    ConsumerProfileUpdate,
)
from .partner_profile import PartnerProfileBase, PartnerProfileResponse, PartnerProfileUpdate
from phone_utils import clean_phone
from pydantic import field_validator


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
    branch: str | None = None


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

    BRANCH:
        branch_id is the FK to the branches table (admin-managed list).
        Use GET /branches to look up branch ids.
    """
    contact_type: ContactType = ContactType.consumer
    # FK to branches table. Use GET /branches to get ids.
    branch_id: int | None = None
    # Optional — phone is used as display name when absent.
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


class ContactCreate(ContactBase):
    """
    Request body for POST /contacts.

    Inherits all ContactBase fields, then adds create-only fields:
        - lead_source_id  → required (overrides the optional base version)
        - assigned_to     → omit to leave unassigned; manager picks it up later
        - *_profile       → type-specific details created in the same transaction
        - lead_id         → set when this contact is created by converting a lead
    """
    # Required on create; overrides ContactBase.lead_source_id which allows None
    lead_source_id: int
    # Omit to create an unassigned contact; manager assigns after reviewing the new lead
    assigned_to: int | None = None
    # Set when creating a contact via lead conversion (normally leave None for manual creation)
    lead_id: int | None = None
    # Type details, created in the same transaction. Each one is only allowed for its own contact type.
    consumer_profile: ConsumerProfileBase | None = None
    partner_profile: PartnerProfileBase | None = None


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
    branch_id: int | None = None
    name: Annotated[str, Field(min_length=1, max_length=150)] | None = None
    phone: Annotated[str, Field(min_length=1, max_length=20)] | None = None
    phone_alt: Annotated[str, Field(max_length=20)] | None = None
    district: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    address: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    assigned_to: int | None = None
    referred_by_id: int | None = None
    lead_source_id: int | None = None

    # Creates the profile if the contact has none, otherwise merges the given fields
    consumer_profile: ConsumerProfileUpdate | None = None
    partner_profile: PartnerProfileUpdate | None = None
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
        `referrals_count` requires an extra query that Pydantic cannot run on its own.
        The route handler must set it explicitly after calling model_validate:

            response.referrals_count = db.query(func.count(Contact.id)) \\
                .filter(Contact.referred_by_id == contact.id).scalar() or 0
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    address: str | None = None
    assigned_to: int | None = None
    created_by: int | None = None
    # Set when this contact was created by converting a lead
    lead_id: int | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    deleted_at: datetime.datetime | None = None

    # Nested objects populated from SQLAlchemy relationships
    assigned_employee: EmployeeSummary | None = None
    lead_source: LeadSourceSummary | None = None
    referred_by: ContactSummary | None = None
    # Branch object (populated from the branch relationship)
    branch: BranchResponse | None = None

    # Must be set by the route after model_validate (not auto-populated)
    referrals_count: int = 0
    is_verified: bool = False
    missing_fields: list[str] = []

    # Populated from the DB column (from_attributes=True handles this automatically)
    notes: str | None = None

    # Only one of these will have data depending on contact_type
    consumer_profile: ConsumerProfileResponse | None = None
    partner_profile: PartnerProfileResponse | None = None

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
            work_area        → partner only
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_type: ContactType
    # Branch object; None if no branch assigned
    branch: BranchResponse | None = None
    name: str | None = None
    phone: str
    address: str | None = None
    district: str | None = None
    # Type-specific headline fields pulled from the JSONB profile data dict
    partner_type: str | None = None   # partners only (data["partner_type"])
    house_stage: str | None = None    # consumers only (data["house_stage"])
    work_area: str | None = None      # partners only (data["work_area"])
    lead_source: LeadSourceSummary | None = None
    assigned_to: NameRef | None = None
    created_by: NameRef | None = None
    # Branch code of the employee who created the contact; None when unknown
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
    order_index: int   # column position (matches pipeline_stages.order from DB)
    contact_type: str


# --- Pipeline stage CRUD schemas (used by /pipeline/stages admin endpoints) ---

class PipelineStageCreate(BaseModel):
    """
    Body for POST /pipeline/stages.
    The slug is auto-generated from the name. The stage is placed at the end of
    the chosen pipeline's column order.
    """
    name: Annotated[str, Field(min_length=1, max_length=60)]
    contact_type: ContactType       # which pipeline: consumer or partner
    description: Annotated[str, Field(max_length=255)] | None = None


class PipelineStageUpdate(BaseModel):
    """Body for PATCH /pipeline/stages/{id}. All fields are optional (patch semantics)."""
    name: Annotated[str, Field(min_length=1, max_length=60)] | None = None
    description: Annotated[str, Field(max_length=255)] | None = None
    order: Annotated[int, Field(ge=1)] | None = None    # column position (1 = leftmost)
    is_active: bool | None = None


class PipelineStageReorder(BaseModel):
    """
    Body for POST /pipeline/stages/reorder.
    Send every stage id for one pipeline in the new left-to-right order.
    The backend assigns order=1,2,3… based on each id's position in the list.
    """
    contact_type: ContactType
    ordered_ids: Annotated[list[int], Field(min_length=1, max_length=100)]


class PipelineStageAdminResponse(BaseModel):
    """
    Full admin view of a pipeline stage.
    Returned by all CRUD endpoints so the settings page has everything it needs:
    is_active to show toggles, locked to disable the toggle for protected stages,
    and usage_count to block or warn before deletion.
    """
    id: int
    name: str
    slug: str
    contact_type: str
    description: str | None = None
    order: int
    is_active: bool
    locked: bool = False    # True for new_lead, won, lost — cannot be turned off
    usage_count: int = 0    # number of contacts that have ever passed through this stage


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


# ── Bulk-assign schemas ───────────────────────────────────────────────────────

class ContactBulkAssignRule(BaseModel):
    """
    One assignment rule: assign `count` unassigned contacts from lead source
    `source_id` to employee `assigned_to`. source_id=None means any source.
    """
    source_id: int | None = None
    assigned_to: int = Field(gt=0)
    count: int = Field(gt=0, le=500)


class ContactBulkAssignRequest(BaseModel):
    assignments: list[ContactBulkAssignRule] = Field(min_length=1, max_length=50)


class ContactBulkAssignResponse(BaseModel):
    assigned: int


class ContactStatsResponse(BaseModel):
    """Counts of unassigned contacts, total and broken down by lead source."""
    total_unassigned: int
    unassigned_by_source: dict[str, int] = Field(default_factory=dict)
