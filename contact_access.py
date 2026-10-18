"""Who can see which contacts. One place for the rule; every list, count, export and map reads from here.

- admin and manager: every contact, with full data.
- team_lead: their own contacts plus the contacts of employees whose team_lead_id is them.
- support: their own contacts plus any contact that has reached a customer stage (won, placed, first_order).
- sales_executive, tele_caller, field_executive: only contacts they created or are assigned to.

Anything a user may not see is treated as not found (404), so a restricted user cannot tell it exists.
Creators keep access after a contact is reassigned.
"""
from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from models import Contact, Employee
from models.lead_stage import LeadStage
from models.stage_type import StageType

FULL_ACCESS_ROLES = {"admin", "manager"}
TEAM_LEAD_ROLE = "team_lead"
SUPPORT_ROLE = "support"
# Stage slugs that count as a customer for support, across contact types.
CUSTOMER_STAGE_SLUGS = ("won", "placed", "first_order")


def has_full_access(user: Employee) -> bool:
    return user.role in FULL_ACCESS_ROLES


def _own(user: Employee):
    return or_(Contact.created_by == user.id, Contact.assigned_to == user.id)


def _team_member_ids(user: Employee):
    """Subquery: ids of active employees whose team lead is this user."""
    return select(Employee.id).where(Employee.team_lead_id == user.id, Employee.deleted_at.is_(None))


def _customer_contact_ids():
    """Subquery: contacts that have ever reached a customer stage."""
    return (
        select(LeadStage.contact_id)
        .join(StageType, StageType.id == LeadStage.stage_id)
        .where(StageType.slug.in_(CUSTOMER_STAGE_SLUGS))
    )


def visible_to(user: Employee):
    """SQL filter for the contacts this user may see. None means no filter (full access)."""
    if has_full_access(user):
        return None
    own = _own(user)
    if user.role == TEAM_LEAD_ROLE:
        members = _team_member_ids(user)
        return or_(own, Contact.created_by.in_(members), Contact.assigned_to.in_(members))
    if user.role == SUPPORT_ROLE:
        return or_(own, Contact.id.in_(_customer_contact_ids()))
    return own


def can_see(db: Session, user: Employee, contact: Contact) -> bool:
    """Same rule as visible_to, checked for one contact. Uses the one filter so the two cannot drift."""
    scope = visible_to(user)
    if scope is None:
        return True
    return db.query(Contact.id).filter(and_(Contact.id == contact.id, scope)).first() is not None


def get_visible_contact(db: Session, user: Employee, contact_id: int) -> Contact:
    """The contact, or 404 when it does not exist, is deleted, or the user may not see it."""
    contact = db.query(Contact).filter(Contact.id == contact_id, Contact.deleted_at.is_(None)).first()
    if contact is None or not can_see(db, user, contact):
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


# ---- Tasks and follow-ups follow the same rule, by assignee instead of by contact ----

def visible_assignee_ids(db: Session, user: Employee) -> set[int] | None:
    """Whose tasks and follow-ups this user may see. None means everyone's (admin and manager)."""
    if has_full_access(user):
        return None
    ids = {user.id}
    if user.role == TEAM_LEAD_ROLE:
        ids.update(
            employee_id
            for (employee_id,) in db.query(Employee.id).filter(
                Employee.team_lead_id == user.id, Employee.deleted_at.is_(None)
            )
        )
    return ids


def ensure_can_assign(db: Session, user: Employee, assignee_id: int) -> None:
    """A team lead may only give work to themselves or their team. Other roles are not restricted here."""
    if user.role != TEAM_LEAD_ROLE:
        return
    if assignee_id not in visible_assignee_ids(db, user):
        raise HTTPException(status_code=403, detail="Team leads can only assign work to their own team")
