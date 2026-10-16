"""Who can see which contacts.

- admin and manager see every contact, with full data.
- Everyone else (team_lead, sales_executive, tele_caller, field_executive, support) sees only contacts where
  they are the creator (created_by) or the assignee (assigned_to). Anything else is treated as not found.
  team_lead gets the same view until a team model exists.
"""
from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from models import Contact, Employee

FULL_ACCESS_ROLES = {"admin", "manager"}


def has_full_access(user: Employee) -> bool:
    return user.role in FULL_ACCESS_ROLES


def visible_to(user: Employee):
    """SQL filter for the contacts this user may see. None means no filter (full access)."""
    if has_full_access(user):
        return None
    return or_(Contact.created_by == user.id, Contact.assigned_to == user.id)


def can_see(user: Employee, contact: Contact) -> bool:
    return has_full_access(user) or contact.created_by == user.id or contact.assigned_to == user.id


def get_visible_contact(db: Session, user: Employee, contact_id: int) -> Contact:
    """The contact, or 404 when it does not exist, is deleted, or the user may not see it."""
    contact = db.query(Contact).filter(Contact.id == contact_id, Contact.deleted_at.is_(None)).first()
    if contact is None or not can_see(user, contact):
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact
