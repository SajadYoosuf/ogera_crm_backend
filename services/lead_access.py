"""Who may work on a lead and its interactions (activity logs, tasks).

RULES
- admin and manager ("heads") may work on any lead or interaction.
- every other role (staff) may only:
    * view leads assigned to them, or leads with an open (not completed) follow-up assigned to them,
      which lasts until the follow-up is completed (listing enforced in lead_service.list_leads)
    * log activities / tasks on a lead assigned to them
    * complete or reschedule a task they are assigned to
- delete and reassign: heads only
- assignee on create: heads only (staff auto-assign runs)
"""
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, object_session

from models.interaction import Interaction
from models.lead import Lead
from models.employee import Employee
from models.role import Role

HEAD_ROLES = frozenset({"admin", "manager"})


def is_head(employee: Employee) -> bool:
    """True for admin and manager, who may work on any lead."""
    return getattr(employee, "_role_slug", None) in HEAD_ROLES


def open_task_lead_ids_for(employee_id: int):
    """Subquery of lead ids that have a not-yet-completed follow-up assigned to this employee.

    A follow-up reassigned to someone keeps the lead visible to them until the task is completed.
    NULL status counts as open (the column defaults to 'todo').
    """
    return (
        select(Interaction.entity_id)
        .where(Interaction.entity_type == "lead")
        .where(Interaction.kind == "task")
        .where(Interaction.assigned_to == employee_id)
        .where(or_(Interaction.status.is_(None), Interaction.status != "completed"))
    )


def _has_open_task_on_lead(lead: Lead, employee: Employee) -> bool:
    db = object_session(lead)
    if db is None:
        return False
    return db.scalar(
        open_task_lead_ids_for(employee.id).where(Interaction.entity_id == lead.id).limit(1)
    ) is not None


def can_view_lead(employee: Employee, lead: Lead) -> bool:
    """Heads see every lead. Staff see leads assigned to them or with an open follow-up for them."""
    return (
        is_head(employee)
        or lead.assigned_to == employee.id
        or _has_open_task_on_lead(lead, employee)
    )


def ensure_can_work_lead(employee: Employee, lead: Lead) -> None:
    """Raise 403 unless the employee is a head, the lead's assignee, or has an open follow-up on it."""
    if can_view_lead(employee, lead):
        return
    raise HTTPException(
        status_code=403,
        detail="Only the assigned employee or an admin or manager can work on this lead",
    )


def ensure_can_log_on_lead(employee: Employee, lead: Lead) -> None:
    """Raise 403 unless the employee is a head, the lead's assignee, its creator, or has an open follow-up on it."""
    if can_view_lead(employee, lead) or lead.created_by == employee.id:
        return
    raise HTTPException(
        status_code=403,
        detail="Only the assigned employee, the creator, or a head can log on this lead",
    )


def ensure_can_assign_task(employee: Employee, assigned_to: int) -> None:
    """Raise 403 unless the employee is a head or is assigning to themselves."""
    if is_head(employee) or assigned_to == employee.id:
        return
    raise HTTPException(
        status_code=403,
        detail="You can only assign tasks to yourself",
    )


def ensure_can_modify_interaction(employee: Employee, interaction_assigned_to: int | None) -> None:
    """Raise 403 unless the employee is a head or is the interaction's assignee."""
    if is_head(employee) or interaction_assigned_to == employee.id:
        return
    raise HTTPException(
        status_code=403,
        detail="Only the assignee or an admin or manager can modify this interaction",
    )


# ── Contact-scoped helpers (legacy contact/follow-up routes still import these) ──

def ensure_can_work_contact(employee: Employee, contact) -> None:
    """Raise 403 unless the employee is a head or owns this contact."""
    if is_head(employee) or contact.assigned_to == employee.id:
        return
    raise HTTPException(
        status_code=403,
        detail="Only the assigned employee or an admin or manager can change this lead",
    )


def ensure_can_log_activity(employee: Employee, contact) -> None:
    """Raise 403 unless the employee is a head, the contact's assignee, or its creator."""
    if is_head(employee) or contact.assigned_to == employee.id or contact.created_by == employee.id:
        return
    raise HTTPException(
        status_code=403,
        detail="Only the assigned employee, the creator, or an admin or manager can log on this lead",
    )


def ensure_can_work_followup(employee: Employee, followup) -> None:
    """Raise 403 unless the employee is a head or is the follow-up's assignee."""
    if is_head(employee) or followup.assigned_to == employee.id:
        return
    raise HTTPException(
        status_code=403,
        detail="Only the assignee or an admin or manager can change this follow-up",
    )


def ensure_can_assign_followup(employee: Employee, assigned_to: int) -> None:
    """Raise 403 unless the employee is a head or is booking the follow-up for themselves."""
    if is_head(employee) or assigned_to == employee.id:
        return
    raise HTTPException(status_code=403, detail="You can only schedule follow-ups for yourself")


def ensure_is_head(employee: Employee, action: str = "perform this action") -> None:
    """Raise 403 unless the employee is a head."""
    if is_head(employee):
        return
    raise HTTPException(
        status_code=403,
        detail=f"Only admin or manager can {action}",
    )


def head_employee_ids(db: Session) -> list[int]:
    """Active employees whose role sees every record (admin and manager in production)."""
    return list(db.scalars(
        select(Employee.id)
        .join(Role, Role.id == Employee.role_id)
        .where(
            Role.see_all_records.is_(True),
            Employee.is_active == 1,
            Employee.deleted_at.is_(None),
        )
    ).all())
