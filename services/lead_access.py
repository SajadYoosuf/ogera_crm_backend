"""Who may work on a lead and its follow-ups.

WHAT THIS FILE DECIDES
- admin and manager ("heads") may log activities, schedule follow-ups and
  change any follow-up, on any lead.
- every other employee (sales staff, support, custom roles) may only:
    * log activities and schedule follow-ups on a lead assigned to them
      (contacts.assigned_to == their id), and
    * complete or reschedule follow-ups assigned to them
      (followup_schedule.assigned_to == their id).
- reading is not affected: every signed-in employee can still view a lead's
  activity log, follow-ups and history.

WHY IT LIVES HERE
The rule is used by several routes (lead activities, follow-ups, tasks). One
place keeps them in step. The routes call these helpers before they change
anything, and a refusal is a 403 with a plain message.

These helpers only read fields that are already on the loaded objects, so
they do not run any database queries of their own.
"""
from fastapi import HTTPException

from models import Contact, Employee, FollowupSchedule

HEAD_ROLES = frozenset({"admin", "manager"})


def is_head(employee: Employee) -> bool:
    """True for admin and manager, who may work on any lead."""
    return employee.role in HEAD_ROLES


def ensure_can_work_contact(employee: Employee, contact: Contact) -> None:
    """Raise 403 unless the employee is a head or owns this lead."""
    if is_head(employee) or contact.assigned_to == employee.id:
        return
    raise HTTPException(
        status_code=403,
        detail="Only the assigned employee or an admin or manager can change this lead",
    )


def ensure_can_log_activity(employee: Employee, contact: Contact) -> None:
    """Raise 403 unless the employee is a head, the lead's assignee, or the lead's creator.

    Used only by the activity log route. Follow-up and task rules stay on
    ensure_can_work_contact, so a creator who is not the assignee can log
    an activity but cannot schedule follow-ups.
    """
    if is_head(employee) or contact.assigned_to == employee.id or contact.created_by == employee.id:
        return
    raise HTTPException(
        status_code=403,
        detail="Only the assigned employee, the creator, or an admin or manager can log on this lead",
    )


def ensure_can_work_followup(employee: Employee, followup: FollowupSchedule) -> None:
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
    raise HTTPException(
        status_code=403,
        detail="You can only schedule follow-ups for yourself",
    )
