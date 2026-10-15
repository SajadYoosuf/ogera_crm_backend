import re
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from auth import require_roles
from database import get_db
from models import Employee, Role
from schemas import (
    RoleCreate,
    RoleListResponse,
    RoleResponse,
    RoleUpdate,
    RoleEnum,
)

router = APIRouter(prefix="/roles", tags=["roles"])

# Reading roles is needed to build the employee form; changing them is admin-only.
view_roles = require_roles(RoleEnum.admin, RoleEnum.manager)
manage_roles = require_roles(RoleEnum.admin)

DUPLICATE_ROLE = "A role with this name already exists"


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40].strip("_")
    if not slug or not slug[0].isalpha():
        raise HTTPException(
            status_code=422, detail="Role name must start with a letter and contain letters or digits"
        )
    return slug


def _to_response(role: Role, employee_count: int) -> RoleResponse:
    response = RoleResponse.model_validate(role, from_attributes=True)
    response.employee_count = employee_count
    return response


def _employee_counts(db: Session) -> dict[str, int]:
    """Active (not soft-deleted) employees per role slug."""
    rows = (
        db.query(Employee.role, func.count(Employee.id))
        .filter(Employee.deleted_at.is_(None))
        .group_by(Employee.role)
        .all()
    )
    return {slug: count for slug, count in rows}


def _get_role_or_404(db: Session, role_id: int) -> Role:
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


def _name_taken(db: Session, name: str, exclude_id: int | None = None) -> bool:
    q = db.query(Role.id).filter(func.lower(Role.name) == name.lower())
    if exclude_id is not None:
        q = q.filter(Role.id != exclude_id)
    return q.first() is not None


@router.get("", response_model=RoleListResponse, summary="List roles")
def list_roles(
    include_inactive: bool = True,
    db: Session = Depends(get_db),
    _: Employee = Depends(view_roles),
):
    """Lists roles for admins and managers. Pass `include_inactive=false` to get only the roles that can be assigned."""
    q = db.query(Role)
    if not include_inactive:
        q = q.filter(Role.is_active == 1)
    rows = q.order_by(Role.is_system.desc(), Role.name).all()
    counts = _employee_counts(db)
    return RoleListResponse(
        items=[_to_response(r, counts.get(r.slug, 0)) for r in rows], total=len(rows)
    )


@router.get("/{role_id}", response_model=RoleResponse, summary="Get a role")
def get_role(
    role_id: int,
    db: Session = Depends(get_db),
    _: Employee = Depends(view_roles),
):
    """One role by id. Admin and manager only."""
    role = _get_role_or_404(db, role_id)
    return _to_response(role, _employee_counts(db).get(role.slug, 0))


@router.post("", response_model=RoleResponse, status_code=201, summary="Create a custom role")
def create_role(
    body: RoleCreate,
    db: Session = Depends(get_db),
    _: Employee = Depends(manage_roles),
):
    """Creates a custom role (admin only). The slug is generated from the name and never changes."""
    slug = _slugify(body.name)
    if _name_taken(db, body.name) or db.query(Role.id).filter(Role.slug == slug).first():
        raise HTTPException(status_code=409, detail=DUPLICATE_ROLE)

    role = Role(slug=slug, name=body.name, description=body.description, is_system=0)
    db.add(role)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_ROLE)
    db.refresh(role)
    return _to_response(role, 0)


@router.put("/{role_id}", response_model=RoleResponse, summary="Update a role")
def update_role(
    role_id: int,
    body: RoleUpdate,
    db: Session = Depends(get_db),
    _: Employee = Depends(manage_roles),
):
    """Edits a role's name or description, or turns it on or off (admin only). The slug never changes."""
    role = _get_role_or_404(db, role_id)
    data = body.model_dump(exclude_unset=True)
    for field in ("name", "is_active"):
        if field in data and data[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null")

    if role.is_system and data.get("is_active") is False:
        raise HTTPException(status_code=400, detail="System roles cannot be deactivated")
    if "name" in data and _name_taken(db, data["name"], exclude_id=role.id):
        raise HTTPException(status_code=409, detail=DUPLICATE_ROLE)

    for field, value in data.items():
        setattr(role, field, int(value) if field == "is_active" else value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_ROLE)
    db.refresh(role)
    return _to_response(role, _employee_counts(db).get(role.slug, 0))


@router.delete("/{role_id}", status_code=204, summary="Delete a role")
def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
    _: Employee = Depends(manage_roles),
):
    """Deletes a custom role that no employee uses (admin only). For a role that is in use, deactivate it instead."""
    role = _get_role_or_404(db, role_id)
    if role.is_system:
        raise HTTPException(status_code=400, detail="System roles cannot be deleted")
    # Count soft-deleted employees too: they still reference the role.
    in_use = db.query(func.count(Employee.id)).filter(Employee.role == role.slug).scalar() or 0
    if in_use:
        raise HTTPException(
            status_code=409,
            detail=f"Role is assigned to {in_use} employee(s). Reassign them or deactivate the role instead",
        )
    db.delete(role)
    db.commit()
