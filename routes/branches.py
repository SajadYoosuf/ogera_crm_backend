"""
Branch routes — read-only endpoints for branch definitions.

WHAT IS THIS FILE?
------------------
Branches are physical office locations (e.g. malappuram, kottakkal) stored in the
`branches` table inside each tenant's PostgreSQL schema.

The frontend calls these endpoints to populate branch dropdowns on the create/edit
contact form.

ROUTE OVERVIEW:
    GET  /branches          — list all active branches for the current tenant
    GET  /branches/{id}     — get a single branch by id
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from auth import get_current_user, get_tenant_db
from models import Employee
from models.branch import Branch
from schemas.branch import BranchResponse

router = APIRouter(
    prefix="/branches",
    tags=["branches"],
    dependencies=[Depends(get_current_user)],
)


@router.get("", response_model=list[BranchResponse], summary="List active branches")
def list_branches(
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(get_current_user),
):
    """
    Returns all active branches for the current tenant.

    The frontend uses this to populate the branch dropdown on the contact form.
    Branch ids are what you send as `branch_id` when creating or updating a contact.

    SQL equivalent:
        SELECT id, name, code, is_active FROM branches WHERE is_active = TRUE ORDER BY name;
    """
    rows = (
        db.query(Branch)
        .filter(Branch.is_active.is_(True))
        .order_by(Branch.name)
        .all()
    )
    return rows


@router.get("/{branch_id}", response_model=BranchResponse, summary="Get a single branch")
def get_branch(
    branch_id: int,
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(get_current_user),
):
    """
    Returns one branch by its id.

    RAISES: 404 if no branch with that id exists (or it was deactivated).
    """
    row = db.query(Branch).filter(Branch.id == branch_id, Branch.is_active.is_(True)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Branch not found")
    return row
