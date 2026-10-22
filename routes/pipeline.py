"""Pipeline stage endpoints.

GET /pipeline/stages  — returns the ordered list of stages for one contact type (consumer,
partner or dealer), or all stages when no type is given. The board uses this to know which
columns to show and in what order.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models import Employee, PipelineStage
from schemas.contact import ContactType, PipelineStageItem

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.get("/stages", response_model=list[PipelineStageItem], summary="List pipeline stages")
def list_pipeline_stages(
    contact_type: ContactType | None = Query(None, description="Filter to one pipeline: consumer, partner or dealer"),
    db: Session = Depends(get_db),
    _: Employee = Depends(get_current_user),
):
    """Returns active pipeline stages in board order.

    - Pass `contact_type=consumer` (or partner / dealer) to get only the stages for that board.
    - Leave it out to get all stages across every pipeline.

    The `id` field is the value to send as `stage_id` when moving a contact to that stage.
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
            order_index=i,
            contact_type=r.contact_type,
        )
        for i, r in enumerate(rows)
    ]
