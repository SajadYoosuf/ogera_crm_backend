"""
Lead statuses CRUD — admin only.

ENDPOINTS:
    GET    /settings/lead-statuses            — list all statuses (any authenticated user)
    POST   /settings/lead-statuses            — create a new status (admin)
    PATCH  /settings/lead-statuses/{id}       — rename / reorder a status (admin)
    DELETE /settings/lead-statuses/{id}       — deactivate a status (admin; 409 if in use)

RULES:
    - 'new' and 'converted' are locked — they cannot be deleted.
    - Max 20 active statuses.
    - Slugs are case-insensitive unique (e.g. 'attempt_to_contact' and 'Attempt_To_Contact' conflict).
    - DELETE returns 409 if any lead currently has that status.
"""
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from auth import get_current_user, require_roles
from database import get_session_for_schema
from models.employee import Employee
from models.lead import Lead
from models.lead_status import LeadStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings/lead-statuses", tags=["lead-statuses"])

LOCKED_SLUGS = frozenset({"new", "converted"})
MAX_ACTIVE = 20


def _slug_from_name(name: str) -> str:
    """'Attempt to Contact' → 'attempt_to_contact'"""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


class StatusCreate(BaseModel):
    name: str
    sort_order: int = 0

    @field_validator("name")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name is required")
        if len(v) > 60:
            raise ValueError("Name must be 60 characters or fewer")
        return v


class StatusUpdate(BaseModel):
    name: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None


@router.get("", summary="List lead statuses")
def list_statuses(current_user: Employee = Depends(get_current_user)):
    schema = current_user._tenant_schema
    try:
        with get_session_for_schema(schema) as db:
            rows = db.scalars(
                select(LeadStatus).order_by(LeadStatus.sort_order, LeadStatus.id)
            ).all()
            return [_to_dict(r) for r in rows]
    except OperationalError as exc:
        logger.error("list_statuses — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")


@router.post("", status_code=201, summary="Create a lead status (admin)")
def create_status(
    body: StatusCreate,
    current_user: Employee = Depends(require_roles("admin")),
):
    schema = current_user._tenant_schema
    try:
        with get_session_for_schema(schema) as db:
            # Max 20 active
            active_count = db.scalar(
                select(func.count()).select_from(LeadStatus).where(LeadStatus.is_active == True)  # noqa: E712
            ) or 0
            if active_count >= MAX_ACTIVE:
                raise HTTPException(400, detail=f"Maximum {MAX_ACTIVE} active lead statuses allowed")

            slug = _slug_from_name(body.name)
            # Case-insensitive unique check
            existing = db.scalar(
                select(LeadStatus).where(func.lower(LeadStatus.slug) == slug.lower())
            )
            if existing:
                raise HTTPException(409, detail=f"A status with the name '{body.name}' already exists")

            row = LeadStatus(slug=slug, name=body.name.strip(), sort_order=body.sort_order)
            db.add(row)
            db.commit()
            db.refresh(row)
            return _to_dict(row)
    except OperationalError as exc:
        logger.error("create_status — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")


@router.patch("/{status_id}", summary="Update a lead status (admin)")
def update_status(
    status_id: int,
    body: StatusUpdate,
    current_user: Employee = Depends(require_roles("admin")),
):
    schema = current_user._tenant_schema
    try:
        with get_session_for_schema(schema) as db:
            row = db.scalar(select(LeadStatus).where(LeadStatus.id == status_id))
            if not row:
                raise HTTPException(404, detail="Status not found")
            if body.name is not None:
                name = body.name.strip()
                new_slug = _slug_from_name(name)
                # Check uniqueness if slug would change
                if new_slug != row.slug:
                    conflict = db.scalar(
                        select(LeadStatus)
                        .where(func.lower(LeadStatus.slug) == new_slug.lower())
                        .where(LeadStatus.id != status_id)
                    )
                    if conflict:
                        raise HTTPException(409, detail=f"A status with the name '{name}' already exists")
                    if row.slug not in LOCKED_SLUGS:
                        row.slug = new_slug
                row.name = name
            if body.sort_order is not None:
                row.sort_order = body.sort_order
            if body.is_active is not None:
                if not body.is_active and row.slug in LOCKED_SLUGS:
                    raise HTTPException(400, detail=f"The '{row.slug}' status cannot be deactivated")
                if body.is_active and not row.is_active:
                    active_count = db.scalar(
                        select(func.count()).select_from(LeadStatus).where(LeadStatus.is_active == True)  # noqa: E712
                    ) or 0
                    if active_count >= MAX_ACTIVE:
                        raise HTTPException(400, detail=f"Maximum {MAX_ACTIVE} active lead statuses allowed")
                row.is_active = body.is_active
            db.commit()
            db.refresh(row)
            return _to_dict(row)
    except OperationalError as exc:
        logger.error("update_status — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")


@router.delete("/{status_id}", status_code=204, summary="Delete a lead status (admin)")
def delete_status(
    status_id: int,
    current_user: Employee = Depends(require_roles("admin")),
):
    schema = current_user._tenant_schema
    try:
        with get_session_for_schema(schema) as db:
            row = db.scalar(select(LeadStatus).where(LeadStatus.id == status_id))
            if not row:
                raise HTTPException(404, detail="Status not found")
            if row.slug in LOCKED_SLUGS:
                raise HTTPException(400, detail=f"The '{row.slug}' status cannot be deleted")
            # 409 if in use
            in_use = db.scalar(
                select(func.count()).select_from(Lead)
                .where(Lead.status == row.slug)
                .where(Lead.deleted_at.is_(None))
            ) or 0
            if in_use:
                raise HTTPException(409, detail=f"{in_use} lead(s) are using this status. Reassign them first.")
            db.delete(row)
            db.commit()
    except OperationalError as exc:
        logger.error("delete_status — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")


def _to_dict(r: LeadStatus) -> dict:
    return {
        "id": r.id,
        "slug": r.slug,
        "name": r.name,
        "sort_order": r.sort_order,
        "is_active": r.is_active,
        "created_at": r.created_at,
        "locked": r.slug in LOCKED_SLUGS,
    }
