"""What each role may do. One table, read by the routes and by the permission matrix (routes/settings.py).

ACTION_ROLES is the default. An admin or manager can override any action per role in Settings > Roles &
Permissions; the override is stored in `role_permissions` and wins over the default. Admin is always allowed.

Record visibility lives in contact_access.py. This file answers "may this role do this action at all".
Custom roles (created in Settings) get no actions by default, so they see only their own records.
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import Employee, RolePermission

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


def is_allowed(db: Session, role: str, action: str) -> bool:
    """True when `role` may do `action`: admin always, otherwise the saved override or the default."""
    if role == "admin":
        return True
    row = db.query(RolePermission).filter(RolePermission.role_slug == role, RolePermission.action == action).first()
    if row is not None:
        return bool(row.allowed)
    return role in ACTION_ROLES.get(action, set())


def can(db: Session, user: Employee, action: str) -> bool:
    return is_allowed(db, user.role, action)


def require_action(db: Session, user: Employee, action: str) -> None:
    if not can(db, user, action):
        raise HTTPException(status_code=403, detail=f"Your role cannot {action.replace('_', ' ')}")


def ensure_can_create_type(db: Session, user: Employee, contact_type: str) -> None:
    if contact_type in ("partner", "dealer"):
        if not can(db, user, "create_partner_or_dealer"):
            raise HTTPException(status_code=403, detail="Your role cannot create partners or dealers")


def ensure_can_move_to(db: Session, user: Employee, stage_slug: str) -> None:
    require_action(db, user, "move_stage")
    if user.role == "field_executive" and stage_slug not in FIELD_EARLY_STAGE_SLUGS:
        raise HTTPException(status_code=403, detail="Field executives can only move leads to early stages")


def ensure_can_schedule_for(db: Session, user: Employee, assignee_id: int) -> None:
    """Support schedules follow-ups for itself only. Other roles follow the team rules in contact_access."""
    require_action(db, user, "schedule_followup")
    if user.role == "support" and assignee_id != user.id:
        raise HTTPException(status_code=403, detail="Support can only schedule follow-ups for itself")
