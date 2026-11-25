"""
In-app notifications for changes to a lead.

Who is told: every active admin/manager (any see-all head) and the lead's current
assignee, except the person who made the change and any explicit exclusions.

    - Staff change a lead -> admins/managers hear about it (and the assignee, if it
      is someone else).
    - Admin/manager change a lead -> the assignee hears about it (and the other heads).

The caller owns the commit, same as services/notification_service.create_notification.
"""
from sqlalchemy.orm import Session

from models.employee import Employee
from models.lead import Lead
from services.lead_access import head_employee_ids
from services.notification_service import create_notification


def lead_display_name(lead: Lead) -> str:
    return " ".join(p for p in (lead.first_name, lead.last_name) if p) or f"Lead #{lead.id}"


def notify_lead_change(
    db: Session,
    lead: Lead,
    actor: Employee,
    notif_type: str,
    title: str,
    message: str,
    exclude: tuple[int | None, ...] = (),
) -> int:
    """Create one in-app notification per recipient. Returns how many were created."""
    recipients = set(head_employee_ids(db))
    if lead.assigned_to:
        recipients.add(lead.assigned_to)
    recipients.discard(actor.id)
    for skip in exclude:
        if skip is not None:
            recipients.discard(skip)
    for recipient_id in sorted(recipients):
        create_notification(db, recipient_id, notif_type, title, message, "lead", lead.id)
    return len(recipients)
