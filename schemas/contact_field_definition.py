"""
Schemas for contact_field_definitions — the admin-editable field metadata.

WHAT THESE ARE USED FOR:
    GET /contact-field-definitions?entity_type=consumer
    Returns all active field definitions so the frontend knows:
      - which fields to show
      - which are dropdowns (and what their options are)
      - which are required
      - what label to display

FRONTEND USAGE:
    For each ContactFieldDefinitionResponse, the frontend renders:
        field_type == 'dropdown' → <Select options={options} />
        field_type == 'number'   → <NumberInput />
        field_type == 'text'     → <TextInput />
        field_type == 'boolean'  → <Checkbox />

    The field_name is the key used in the profile data dict:
        consumer_profile.data["tank_type"] = "plastic"
"""

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

FieldType = Literal["text", "dropdown", "number", "boolean"]
VALID_ENTITY_TYPES = ("consumer", "partner", "dealer")


class ContactFieldDefinitionCreate(BaseModel):
    """Body for POST /contact-field-definitions (admin only)."""
    entity_type: str                        # 'consumer', 'partner', or 'dealer'
    field_name: Annotated[str, Field(min_length=1, max_length=50, pattern=r"^[a-z_][a-z0-9_]*$")]
    field_label: Annotated[str, Field(min_length=1, max_length=100)]
    field_type: FieldType
    options: list[Annotated[str, Field(min_length=1, max_length=80)]] | None = None
    is_required: bool = False
    sort_order: int | None = None           # auto-assigned (max+1) when omitted
    show_in_list: bool = False


class ContactFieldDefinitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_type: str              # 'consumer' or 'partner'
    field_name: str               # key in profile data (e.g. 'tank_type')
    field_label: str | None       # display label (e.g. 'Tank Type')
    field_type: str               # 'text' | 'dropdown' | 'number' | 'boolean'
    options: list[str] | None     # dropdown options (None for text/number/boolean)
    is_required: bool
    sort_order: int
    is_active: bool


class ContactFieldDefinitionUpdate(BaseModel):
    """Admin can update label, options, required flag, sort order, and active status."""
    field_label: str | None = None
    options: list[str] | None = None
    is_required: bool | None = None
    sort_order: int | None = None
    is_active: bool | None = None
