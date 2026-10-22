"""Find one live contact by id.

In SQL terms: SELECT * FROM contacts WHERE id = :id AND deleted_at IS NULL.
Every signed-in employee can open every contact, so there is no per-user filter.
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import Contact


def get_live_contact(db: Session, contact_id: int) -> Contact:
    """The contact, or 404 when the id is unknown or the contact was deleted."""
    contact = db.query(Contact).filter(Contact.id == contact_id, Contact.deleted_at.is_(None)).first()
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact
