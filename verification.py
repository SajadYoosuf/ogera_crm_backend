"""
Contact verification — defines what "verified" means for each contact type.

HOW VERIFICATION WORKS:
    A contact starts as "unverified" when created with only phone + lead_source.
    Once name, phone, and address are all filled in the contact is considered
    verified and can advance past the new_lead pipeline stage.

REQUIRED FIELDS (all contact types):
    - phone
    - name
    - address

    district, lead_source, and profile fields (house_stage, tank_type, etc.)
    are collected gradually and are NOT required for verification — the rep
    should be able to move the contact forward once they have basic contact info.

TWO IMPLEMENTATIONS (kept in sync):
    `missing_fields(contact)` — Python, runs on a loaded ORM object.
                                Used in single-contact reads and stage transitions.
    `verified_clause()`       — SQLAlchemy expression, runs inside DB queries.
                                Used for the verified=true/false list filter.
"""

from sqlalchemy import and_, func
from models import Contact


def _filled_str(value: str | None) -> bool:
    return bool((value or "").strip())


def missing_fields(contact: Contact) -> list[str]:
    """Names of the basic details still missing. Empty list → verified."""
    return [
        field
        for field in ("phone", "name", "address")
        if not _filled_str(getattr(contact, field))
    ]


def _filled_col(column):
    """True in SQL when a column is not null, not empty, and not whitespace-only."""
    return func.length(func.trim(func.coalesce(column, ""))) > 0


def verified_clause():
    """SQL twin of `missing_fields(contact) == []`.

    Keep in sync with `missing_fields()` above.
    """
    return and_(
        _filled_col(Contact.phone),
        _filled_col(Contact.name),
        _filled_col(Contact.address),
    )
