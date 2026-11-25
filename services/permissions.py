"""
Permission helpers — replaces all hardcoded role-slug checks.

SHAPE OF permissions JSON on a Role:
  {"leads": "crud", "contacts": "cru", "employees": "r", "settings": ""}

  c = create   r = read   u = update   d = delete   "" = no access

USAGE:
  from services.permissions import can, require_permission

  # In a route body:
  if not can(current_user, "leads", "d"):
      raise HTTPException(403, "No permission")

  # As a FastAPI dependency (replaces require_roles):
  Depends(require_permission("settings", "u"))
"""
from fastapi import Depends, HTTPException, status

from auth import get_current_user
from models.employee import Employee


def can(employee: Employee, module: str, action: str) -> bool:
    """Return True if the employee's role grants `action` on `module`.

    action must be one of 'c', 'r', 'u', 'd'.
    Reads _permissions cached on the employee by get_current_user; falls back
    to an empty dict (deny) if the attribute is not set.
    """
    perms: dict = getattr(employee, "_permissions", {}) or {}
    return action in perms.get(module, "")


def require_permission(module: str, action: str):
    """FastAPI dependency factory — allows only employees whose role has the given permission.

    Usage:
        @router.post("/...", dependencies=[Depends(require_permission("settings", "u"))])
    or:
        current_user: Employee = Depends(require_permission("leads", "d"))
    """
    def checker(current_user: Employee = Depends(get_current_user)) -> Employee:
        if not can(current_user, module, action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )
        return current_user

    return checker
