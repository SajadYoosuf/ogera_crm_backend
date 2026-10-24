"""Recent contact events for the mobile activity log picker.

WHAT THIS SHOWS
Two kinds of event, newest first:
- "created":  a contact was added (contacts.created_at, by contacts.created_by).
- "assigned": a contact was given to an employee (one contact_assignments row
              with an owner; rows with no owner are skipped).

WHO SEES WHAT
- admin and manager ("heads") see every event.
- every other employee sees only events on contacts they created, or events
  where the contact was assigned to them. This matches who may log an activity
  on a lead (services/lead_access.py), so every row in the list can be picked.

WHY A SERVICE
The route stays a thin wrapper. The two queries and the merge live here.

SQL IN PLAIN TERMS
  created:  SELECT ... FROM contacts WHERE deleted_at IS NULL
            [AND created_by = :me] ORDER BY created_at DESC LIMIT :n
  assigned: SELECT ... FROM contact_assignments a JOIN contacts c ON c.id = a.contact_id
            WHERE c.deleted_at IS NULL AND a.assigned_to IS NOT NULL
            [AND a.assigned_to = :me] ORDER BY a.changed_at DESC LIMIT :n
"""
from sqlalchemy.orm import Session, joinedload

from models import Contact, ContactAssignment, Employee
from schemas.activity_log import RecentActivityItem
from services.lead_access import is_head


def list_recent_activity(db: Session, employee: Employee, limit: int) -> list[RecentActivityItem]:
    """Up to `limit` events the employee may see, newest first.

    Each query is capped at `limit`, so the merged list is never longer than
    needed before the final cut.
    """
    items = _created_events(db, employee, limit) + _assigned_events(db, employee, limit)
    items.sort(key=lambda item: item.timestamp, reverse=True)
    return items[:limit]


def _created_events(db: Session, employee: Employee, limit: int) -> list[RecentActivityItem]:
    """One "created" item per live contact, limited to the employee's own unless they are a head."""
    query = (
        db.query(Contact)
        .options(joinedload(Contact.creator))
        .filter(Contact.deleted_at.is_(None))
    )
    if not is_head(employee):
        query = query.filter(Contact.created_by == employee.id)
    contacts = query.order_by(Contact.created_at.desc(), Contact.id.desc()).limit(limit).all()

    return [
        RecentActivityItem(
            event_type="created",
            timestamp=contact.created_at,
            contact_id=contact.id,
            contact_name=contact.name,
            actor_id=contact.created_by,
            actor_name=contact.creator.name if contact.creator else None,
        )
        for contact in contacts
    ]


def _assigned_events(db: Session, employee: Employee, limit: int) -> list[RecentActivityItem]:
    """One "assigned" item per owner change, limited to the employee's own unless they are a head."""
    query = (
        db.query(ContactAssignment, Contact)
        .join(Contact, Contact.id == ContactAssignment.contact_id)
        .options(
            joinedload(ContactAssignment.to_employee),
            joinedload(ContactAssignment.changer),
        )
        .filter(Contact.deleted_at.is_(None), ContactAssignment.assigned_to.isnot(None))
    )
    if not is_head(employee):
        query = query.filter(ContactAssignment.assigned_to == employee.id)
    rows = (
        query.order_by(ContactAssignment.changed_at.desc(), ContactAssignment.id.desc())
        .limit(limit)
        .all()
    )

    return [
        RecentActivityItem(
            event_type="assigned",
            timestamp=assignment.changed_at,
            contact_id=contact.id,
            contact_name=contact.name,
            actor_id=assignment.changed_by,
            actor_name=assignment.changer.name if assignment.changer else None,
            assigned_to_name=assignment.to_employee.name if assignment.to_employee else None,
        )
        for assignment, contact in rows
    ]
