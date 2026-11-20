"""
Contact field definition routes — read (and admin-update) endpoints for tenant-configurable
contact profile fields.

WHAT IS THIS FILE?
------------------
`contact_field_definitions` describes which fields exist on consumer and partner profiles,
their types (text, dropdown, number, boolean), and their dropdown options.

The frontend calls these before rendering the create/edit contact form so it knows
which fields to show and what choices are valid.

An admin can update a field's label, options, required flag, or sort order without
any code change — the form adapts at the next load.

ROUTE OVERVIEW:
    GET   /contact-field-definitions              — list all active field definitions
    GET   /contact-field-definitions/{entity_type} — list for one entity type
    PATCH /contact-field-definitions/{id}         — admin: update a field definition
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from auth import get_current_user, require_roles
from database import get_db
from models import Employee
from models.contact_field_definition import ContactFieldDefinition
from schemas.contact_field_definition import ContactFieldDefinitionResponse, ContactFieldDefinitionUpdate

router = APIRouter(
    prefix="/contact-field-definitions",
    tags=["contact-fields"],
    dependencies=[Depends(get_current_user)],
)


@router.get("", response_model=list[ContactFieldDefinitionResponse], summary="List all field definitions")
def list_field_definitions(
    db: Session = Depends(get_db),
    _: Employee = Depends(get_current_user),
):
    """
    Returns all active field definitions for both consumer and partner profiles.

    The frontend renders the contact form based on this data:
      - field_type='dropdown' → render a select/dropdown with the `options` list
      - field_type='number'   → render a number input
      - field_type='text'     → render a text input
      - is_required=True      → mark the field as required in the UI

    SQL equivalent:
        SELECT * FROM contact_field_definitions WHERE is_active = TRUE ORDER BY entity_type, sort_order;
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
    db: Session = Depends(get_db),
    _: Employee = Depends(get_current_user),
):
    """
    Returns active field definitions for `entity_type` = 'consumer' or 'partner'.

    The frontend calls this with the contact type to get the relevant form fields.

    RAISES: 400 if entity_type is not 'consumer' or 'partner'.
    """
    if entity_type not in ("consumer", "partner"):
        raise HTTPException(
            status_code=400,
            detail="entity_type must be 'consumer' or 'partner'",
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


@router.patch(
    "/by-id/{definition_id}",
    response_model=ContactFieldDefinitionResponse,
    summary="Update a field definition (admin only)",
    dependencies=[Depends(require_roles("admin"))],
)
def update_field_definition(
    definition_id: int,
    body: ContactFieldDefinitionUpdate,
    db: Session = Depends(get_db),
    _: Employee = Depends(get_current_user),
):
    """
    Admin-only: update label, options, required flag, sort order, or active status.

    Only the fields sent in the request body are changed (PATCH semantics).

    EXAMPLE USE CASES:
      - Admin adds a new dropdown option to 'tank_type': send `options: [...]` with the full new list.
      - Admin renames 'House Stage' label: send `field_label: 'Property Status'`.
      - Admin deactivates a field: send `is_active: false`.

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
