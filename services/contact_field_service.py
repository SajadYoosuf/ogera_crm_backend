"""
Contact field service — validation logic for JSONB profile data against field definitions.

WHAT IS THIS?
    When a consumer or partner profile is created or updated, the data dict
    (e.g. {"tank_type": "plastic", "tank_age": 3}) needs to be validated against
    the tenant's contact_field_definitions table.

    This service reads those definitions and checks:
      1. Required fields: if is_required=True, the key must exist and be non-empty.
      2. Dropdown values: if field_type='dropdown', the value must be in the options list.
      3. Number type: if field_type='number', the value must be a number.

    Unknown keys (keys in data that have no definition) are allowed — this lets
    the admin add fields later without breaking old records.

USAGE:
    from services.contact_field_service import validate_profile_data

    # In a route/service, before saving:
    validate_profile_data(db, entity_type="consumer", data=body.consumer_profile.data)
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models.contact_field_definition import ContactFieldDefinition


def get_field_definitions(db: Session, entity_type: str) -> list[ContactFieldDefinition]:
    """
    Returns all active field definitions for the given entity type.

    SQL equivalent: SELECT * FROM contact_field_definitions
                    WHERE entity_type = ? AND is_active = true
                    ORDER BY sort_order;
    """
    return (
        db.query(ContactFieldDefinition)
        .filter(
            ContactFieldDefinition.entity_type == entity_type,
            ContactFieldDefinition.is_active.is_(True),
        )
        .order_by(ContactFieldDefinition.sort_order)
        .all()
    )


def validate_profile_data(db: Session, entity_type: str, data: dict) -> None:
    """
    Validate profile data against the tenant's field definitions.

    Raises HTTPException(422) on the first violation found.

    What is validated:
      - Required fields must be present and non-empty.
      - Dropdown fields must use a value from the options list.
      - Number fields must be numeric (int or float).

    Unknown keys (fields not in definitions) are ignored — forward-compatible.

    INPUT:
        db          — DB session (already set to the tenant's search_path)
        entity_type — 'consumer' or 'partner'
        data        — the JSONB dict to validate (e.g. {"tank_type": "plastic"})
    """
    if not data:
        # Empty dict — only fails if any required fields exist
        definitions = get_field_definitions(db, entity_type)
        for defn in definitions:
            if defn.is_required:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "field": defn.field_name,
                        "message": f"'{defn.field_label or defn.field_name}' is required",
                    },
                )
        return

    definitions = get_field_definitions(db, entity_type)
    defn_map = {d.field_name: d for d in definitions}

    for defn in definitions:
        value = data.get(defn.field_name)

        if defn.is_required and (value is None or str(value).strip() == ""):
            raise HTTPException(
                status_code=422,
                detail={
                    "field": defn.field_name,
                    "message": f"'{defn.field_label or defn.field_name}' is required",
                },
            )

        if value is None:
            continue

        if defn.field_type == "dropdown" and defn.options:
            if str(value) not in defn.options:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "field": defn.field_name,
                        "message": f"'{value}' is not a valid option for '{defn.field_label or defn.field_name}'",
                        "valid_options": defn.options,
                    },
                )

        if defn.field_type == "number" and not isinstance(value, (int, float)):
            raise HTTPException(
                status_code=422,
                detail={
                    "field": defn.field_name,
                    "message": f"'{defn.field_label or defn.field_name}' must be a number",
                },
            )
