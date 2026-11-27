"""
Contact field definition routes — CRUD endpoints for tenant-configurable
contact profile fields.

WHAT IS THIS FILE?
------------------
`contact_field_definitions` describes which fields exist on consumer, partner, and dealer
profiles, their types (text, dropdown, number, boolean), and their dropdown options.

The frontend calls these before rendering the create/edit contact form so it knows
which fields to show and what choices are valid.

An admin can add new fields, update a field's label, options, required flag, or sort
order, and deactivate fields without any code change — the form adapts at the next load.

ROUTE OVERVIEW:
    GET   /contact-field-definitions              — list all active field definitions (any employee)
    GET   /contact-field-definitions/all          — list ALL including inactive (admin only)
    GET   /contact-field-definitions/{entity_type} — list active for one entity type
    POST  /contact-field-definitions              — add a new field definition (admin only)
    PATCH /contact-field-definitions/{id}         — admin: update a field definition
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth import get_current_user, get_tenant_db, require_roles
from models import Employee
from models.contact_field_definition import ContactFieldDefinition
from schemas.contact_field_definition import (
    ContactFieldDefinitionCreate,
    ContactFieldDefinitionResponse,
    ContactFieldDefinitionUpdate,
    VALID_ENTITY_TYPES,
)

router = APIRouter(
    prefix="/contact-field-definitions",
    tags=["contact-fields"],
    dependencies=[Depends(get_current_user)],
)


# ---------------------------------------------------------------------------
# /all must be defined BEFORE /{entity_type} so FastAPI doesn't swallow "all"
# as a path parameter.
# ---------------------------------------------------------------------------

@router.get(
    "/all",
    response_model=list[ContactFieldDefinitionResponse],
    summary="List ALL field definitions including inactive (admin only)",
    dependencies=[Depends(require_roles("admin"))],
)
def list_all_field_definitions(
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(get_current_user),
):
    """
    Admin-only: returns every field definition regardless of is_active status.
    Used by the Settings → Contact Fields page so admins can see and re-activate
    hidden fields.
    """
    rows = (
        db.query(ContactFieldDefinition)
        .order_by(ContactFieldDefinition.entity_type, ContactFieldDefinition.sort_order)
        .all()
    )
    return rows


@router.get("", response_model=list[ContactFieldDefinitionResponse], summary="List active field definitions")
def list_field_definitions(
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(get_current_user),
):
    """
    Returns all active field definitions for consumer, partner, and dealer profiles.

    The frontend renders the contact form based on this data:
      - field_type='dropdown' → render a select/dropdown with the `options` list
      - field_type='number'   → render a number input
      - field_type='text'     → render a text input
      - is_required=True      → mark the field as required in the UI
    """
    rows = (
        db.query(ContactFieldDefinition)
        .filter(ContactFieldDefinition.is_active.is_(True))
        .order_by(ContactFieldDefinition.entity_type, ContactFieldDefinition.sort_order)
        .all()
    )
    return rows


@router.get(
    "/{entity_type}",
    response_model=list[ContactFieldDefinitionResponse],
    summary="List field definitions for one entity type",
)
def list_field_definitions_for_entity(
    entity_type: str,
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(get_current_user),
):
    """
    Returns active field definitions for a given entity_type (consumer, partner, or dealer).

    RAISES: 400 if entity_type is not one of the valid values.
    """
    if entity_type not in VALID_ENTITY_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"entity_type must be one of: {', '.join(VALID_ENTITY_TYPES)}",
        )
    rows = (
        db.query(ContactFieldDefinition)
        .filter(
            ContactFieldDefinition.entity_type == entity_type,
            ContactFieldDefinition.is_active.is_(True),
        )
        .order_by(ContactFieldDefinition.sort_order)
        .all()
    )
    return rows


@router.post(
    "",
    response_model=ContactFieldDefinitionResponse,
    status_code=201,
    summary="Create a new field definition (admin only)",
    dependencies=[Depends(require_roles("admin"))],
)
def create_field_definition(
    body: ContactFieldDefinitionCreate,
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(get_current_user),
):
    """
    Admin-only: add a new field to consumer, partner, or dealer contact profiles.

    Rules:
      - entity_type must be 'consumer', 'partner', or 'dealer'.
      - field_name must be unique within the entity_type (snake_case identifier).
      - field_type 'dropdown' should include at least one option.
      - sort_order is auto-assigned as max+1 when omitted.

    RAISES: 400 for invalid entity_type, 409 if field_name already exists for that type.
    """
    if body.entity_type not in VALID_ENTITY_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"entity_type must be one of: {', '.join(VALID_ENTITY_TYPES)}",
        )

    # Check uniqueness of field_name within entity_type
    existing = (
        db.query(ContactFieldDefinition)
        .filter(
            ContactFieldDefinition.entity_type == body.entity_type,
            ContactFieldDefinition.field_name == body.field_name,
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A field named '{body.field_name}' already exists for {body.entity_type} profiles",
        )

    # Auto-assign sort_order if not provided
    if body.sort_order is None:
        max_order = (
            db.query(func.max(ContactFieldDefinition.sort_order))
            .filter(ContactFieldDefinition.entity_type == body.entity_type)
            .scalar()
        ) or 0
        sort_order = max_order + 1
    else:
        sort_order = body.sort_order

    defn = ContactFieldDefinition(
        entity_type=body.entity_type,
        field_name=body.field_name,
        field_label=body.field_label,
        field_type=body.field_type,
        options=body.options,
        is_required=body.is_required,
        sort_order=sort_order,
        is_active=True,
        show_in_list=body.show_in_list,
    )
    db.add(defn)
    db.commit()
    db.refresh(defn)
    return defn


@router.patch(
    "/by-id/{definition_id}",
    response_model=ContactFieldDefinitionResponse,
    summary="Update a field definition (admin only)",
    dependencies=[Depends(require_roles("admin"))],
)
def update_field_definition(
    definition_id: int,
    body: ContactFieldDefinitionUpdate,
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(get_current_user),
):
    """
    Admin-only: update label, options, required flag, sort order, or active status.

    Only the fields sent in the request body are changed (PATCH semantics).

    RAISES: 404 if the definition does not exist.
    """
    defn = db.query(ContactFieldDefinition).filter(ContactFieldDefinition.id == definition_id).first()
    if defn is None:
        raise HTTPException(status_code=404, detail="Field definition not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(defn, field, value)

    db.add(defn)
    db.commit()
    db.refresh(defn)
    return defn
