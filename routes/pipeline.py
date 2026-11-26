"""Pipeline stage CRUD endpoints.

WHAT IS A PIPELINE STAGE?
A pipeline stage is one column on the Kanban board — e.g. "New Lead", "Interested",
"Won". Each stage belongs to exactly one contact type (consumer or partner), and
the `order` column controls the left-to-right column position on the board.

There are two separate pipelines:
  - consumer  → columns for individual buyer contacts
  - partner   → columns for professional/trade contacts

ENDPOINT SUMMARY
  GET  /pipeline/stages               — active stages, board order (any employee)
  GET  /pipeline/stages/all           — all stages incl. inactive, with usage (admin/manager)
  POST /pipeline/stages               — add a new stage (admin/manager)
  POST /pipeline/stages/reorder       — set column order within one pipeline (admin/manager)
  PATCH  /pipeline/stages/{id}        — rename, reorder, or toggle (admin/manager)
  DELETE /pipeline/stages/{id}        — soft-delete if unused (admin/manager)

NOTE ON ROUTE ORDER
/stages/all and /stages/reorder are defined BEFORE /stages/{stage_id} so that
FastAPI does not swallow the literal "all" / "reorder" as a path parameter.

LOCKED STAGES
new_lead, won, and lost are locked — the pipeline follow-up clock and reporting
logic branches on these slugs. They can be renamed but not deactivated or removed.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import settings_catalog as catalog
from auth import get_current_user, get_tenant_db, require_admin_or_manager
from models import Employee, LeadStage, PipelineStage
from schemas.contact import (
    ContactType,
    PipelineStageAdminResponse,
    PipelineStageCreate,
    PipelineStageItem,
    PipelineStageReorder,
    PipelineStageUpdate,
)

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

# Slugs the pipeline logic branches on — can be renamed but not turned off.
_LOCKED = catalog.LOCKED.get(catalog.LEAD_STAGE, set())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _usage(db: Session) -> dict[int, int]:
    """
    Returns {stage_id: distinct_contact_count} for every stage.
    Uses lead_stage_log — counts any contact that has EVER passed through the stage.
    Uses an outer join so stages with 0 contacts still appear.
    """
    rows = (
        db.query(PipelineStage.id, func.count(func.distinct(LeadStage.contact_id)))
        .outerjoin(LeadStage, LeadStage.stage_id == PipelineStage.id)
        .group_by(PipelineStage.id)
        .all()
    )
    return dict(rows)


def _to_admin(row: PipelineStage, usage_count: int = 0) -> PipelineStageAdminResponse:
    """Converts a PipelineStage ORM row to the admin response shape."""
    return PipelineStageAdminResponse(
        id=row.id,
        name=row.name,
        slug=row.slug,
        contact_type=row.contact_type,
        description=row.description,
        order=row.order,
        is_active=bool(row.is_active),
        locked=row.slug in _LOCKED,
        usage_count=usage_count,
    )


def _row_or_404(db: Session, stage_id: int) -> PipelineStage:
    row = db.query(PipelineStage).filter(PipelineStage.id == stage_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Pipeline stage not found")
    return row


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A stage with this name already exists in this pipeline",
        )


# ---------------------------------------------------------------------------
# Read-only — any logged-in employee
# ---------------------------------------------------------------------------

@router.get("/stages", response_model=list[PipelineStageItem], summary="Active pipeline stages (board)")
def list_pipeline_stages(
    contact_type: ContactType | None = Query(None, description="Filter: consumer or partner"),
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(get_current_user),
):
    """
    Returns active pipeline stages in board (column) order.

    Pass ?contact_type=consumer or ?contact_type=partner to get one pipeline's
    columns. Leave it out to get all active stages across both pipelines.

    The `id` in each item is the value to send as `stage_id` when moving a contact
    to that stage. The `order_index` matches the stage's stored column order.
    """
    q = db.query(PipelineStage).filter(PipelineStage.is_active == 1)
    if contact_type is not None:
        q = q.filter(PipelineStage.contact_type == contact_type.value)
    rows = q.order_by(PipelineStage.order, PipelineStage.id).all()
    return [
        PipelineStageItem(
            id=r.id,
            slug=r.slug,
            label=r.name,
            order_index=r.order,   # use stored DB value, not list position
            contact_type=r.contact_type,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Admin read — define /all and /reorder BEFORE /{stage_id}
# ---------------------------------------------------------------------------

@router.get("/stages/all", response_model=list[PipelineStageAdminResponse], summary="All pipeline stages (admin)")
def list_all_pipeline_stages(
    contact_type: ContactType | None = Query(None, description="Filter: consumer or partner"),
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(require_admin_or_manager),
):
    """
    Returns every stage including inactive ones, with usage counts.
    Used by the admin Settings page to show and manage the pipeline columns.
    Admin and manager only.
    """
    q = db.query(PipelineStage)
    if contact_type is not None:
        q = q.filter(PipelineStage.contact_type == contact_type.value)
    rows = q.order_by(PipelineStage.contact_type, PipelineStage.order, PipelineStage.id).all()
    usage = _usage(db)
    return [_to_admin(r, usage.get(r.id, 0)) for r in rows]


@router.post(
    "/stages/reorder",
    response_model=list[PipelineStageAdminResponse],
    summary="Reorder pipeline stages",
)
def reorder_pipeline_stages(
    body: PipelineStageReorder,
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(require_admin_or_manager),
):
    """
    Sets the column order within one pipeline.

    Send every stage id for that pipeline in the new left-to-right order.
    The backend assigns order=1,2,3,… based on the position in ordered_ids.

    All ids must belong to the specified contact_type pipeline — mixing ids
    from different pipelines returns 400.
    """
    if len(set(body.ordered_ids)) != len(body.ordered_ids):
        raise HTTPException(status_code=422, detail="ordered_ids contains duplicates")

    rows = {
        r.id: r
        for r in db.query(PipelineStage).filter(
            PipelineStage.id.in_(body.ordered_ids),
            PipelineStage.contact_type == body.contact_type.value,
        ).all()
    }
    if len(rows) != len(body.ordered_ids):
        raise HTTPException(
            status_code=400,
            detail="One or more ids do not belong to this pipeline",
        )

    for position, stage_id in enumerate(body.ordered_ids, start=1):
        rows[stage_id].order = position

    _commit(db)

    all_rows = (
        db.query(PipelineStage)
        .filter(PipelineStage.contact_type == body.contact_type.value)
        .order_by(PipelineStage.order, PipelineStage.id)
        .all()
    )
    usage = _usage(db)
    return [_to_admin(r, usage.get(r.id, 0)) for r in all_rows]


# ---------------------------------------------------------------------------
# Admin CRUD — admin/manager only
# ---------------------------------------------------------------------------

@router.post("/stages", response_model=PipelineStageAdminResponse, status_code=201, summary="Add a pipeline stage")
def create_pipeline_stage(
    body: PipelineStageCreate,
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(require_admin_or_manager),
):
    """
    Adds a new stage at the end of one pipeline's column order.

    The slug is auto-generated from the name so only a human-readable label is
    needed. If a deactivated stage with the same slug already exists in that
    pipeline it is re-activated and renamed instead of creating a duplicate.
    """
    slug = catalog.slugify(body.name, 40)

    existing = (
        db.query(PipelineStage)
        .filter(
            PipelineStage.slug == slug,
            PipelineStage.contact_type == body.contact_type.value,
        )
        .first()
    )
    if existing is not None:
        if existing.is_active:
            raise HTTPException(
                status_code=409,
                detail="A stage with this name already exists in this pipeline",
            )
        existing.is_active = 1
        existing.name = body.name
        if body.description is not None:
            existing.description = body.description
        _commit(db)
        return _to_admin(existing, 0)

    # Place the new stage at the end of this pipeline's order.
    last_order = (
        db.query(func.max(PipelineStage.order))
        .filter(PipelineStage.contact_type == body.contact_type.value)
        .scalar()
    ) or 0

    row = PipelineStage(
        name=body.name,
        slug=slug,
        contact_type=body.contact_type.value,
        description=body.description,
        order=last_order + 1,
        is_active=1,
    )
    db.add(row)
    _commit(db)
    return _to_admin(row, 0)


@router.patch("/stages/{stage_id}", response_model=PipelineStageAdminResponse, summary="Update a pipeline stage")
def update_pipeline_stage(
    stage_id: int,
    body: PipelineStageUpdate,
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(require_admin_or_manager),
):
    """
    Renames, repositions, or toggles a pipeline stage.

    The slug is never changed — it is the stable key existing log records already use.
    Locked stages (new_lead, won, lost) cannot be deactivated because the
    follow-up clock and reporting logic depend on those slugs.
    """
    row = _row_or_404(db, stage_id)
    data = body.model_dump(exclude_unset=True)

    if "is_active" in data and not data["is_active"] and row.slug in _LOCKED:
        raise HTTPException(
            status_code=400,
            detail="The app relies on this stage; it can't be turned off",
        )

    if "name" in data:
        row.name = data["name"]
    if "description" in data:
        row.description = data["description"]
    if "order" in data:
        row.order = data["order"]
    if "is_active" in data:
        row.is_active = int(data["is_active"])

    _commit(db)
    usage = _usage(db)
    return _to_admin(row, usage.get(row.id, 0))


@router.delete("/stages/{stage_id}", response_model=PipelineStageAdminResponse, summary="Delete a pipeline stage")
def delete_pipeline_stage(
    stage_id: int,
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(require_admin_or_manager),
):
    """
    Soft-deletes a pipeline stage (sets is_active=0).
    The row is kept so the stage history log still reads correctly.

    Blocked if:
    - The stage is locked (new_lead, won, lost) — business logic depends on it.
    - Any contacts have ever passed through this stage — deactivate it instead.
    """
    row = _row_or_404(db, stage_id)

    if row.slug in _LOCKED:
        raise HTTPException(
            status_code=400,
            detail="The app relies on this stage; it can't be removed",
        )

    usage = _usage(db)
    count = usage.get(row.id, 0)
    if count:
        raise HTTPException(
            status_code=409,
            detail=f"{count} contact(s) have passed through this stage. Deactivate it instead of deleting",
        )

    row.is_active = 0
    _commit(db)
    return _to_admin(row, 0)
