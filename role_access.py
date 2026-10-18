"""What each role may do. One table, read by the routes and by the permission matrix (routes/settings.py).

Record visibility lives in contact_access.py. This file answers "may this role do this action at all".
Custom roles (created in Settings) get no actions here; they see only their own records.
"""
from fastapi import HTTPException

from models import Employee

ALL_STAFF = {"admin", "manager", "team_lead", "sales_executive", "tele_caller", "field_executive", "support"}

ACTION_ROLES: dict[str, set[str]] = {
    "create_contact": {"admin", "manager", "team_lead", "sales_executive", "tele_caller", "field_executive"},
    # tele_caller creates consumers only. Partners and dealers come from the others.
    "create_partner_or_dealer": {"admin", "manager", "team_lead", "sales_executive", "field_executive"},
    "move_stage": {"admin", "manager", "team_lead", "sales_executive", "tele_caller", "field_executive"},
    "schedule_followup": {"admin", "manager", "team_lead", "sales_executive", "tele_caller", "field_executive", "support"},
    "log_activity": ALL_STAFF,
    "export": {"admin", "manager", "team_lead"},
}

# field_executive moves leads only through the early stages. Everything later is sales' job.
FIELD_EARLY_STAGE_SLUGS = {"new_lead", "aware", "connected"}


def can(user: Employee, action: str) -> bool:
    return user.role in ACTION_ROLES.get(action, set())


def require_action(user: Employee, action: str) -> None:
    if not can(user, action):
        raise HTTPException(status_code=403, detail=f"Your role cannot {action.replace('_', ' ')}")


def ensure_can_create_type(user: Employee, contact_type: str) -> None:
    if contact_type in ("partner", "dealer"):
        if not can(user, "create_partner_or_dealer"):
            raise HTTPException(status_code=403, detail="Your role cannot create partners or dealers")


def ensure_can_move_to(user: Employee, stage_slug: str) -> None:
    require_action(user, "move_stage")
    if user.role == "field_executive" and stage_slug not in FIELD_EARLY_STAGE_SLUGS:
        raise HTTPException(status_code=403, detail="Field executives can only move leads to early stages")


def ensure_can_schedule_for(user: Employee, assignee_id: int) -> None:
    """Support schedules follow-ups for itself only. Other roles follow the team rules in contact_access."""
    require_action(user, "schedule_followup")
    if user.role == "support" and assignee_id != user.id:
        raise HTTPException(status_code=403, detail="Support can only schedule follow-ups for itself")
