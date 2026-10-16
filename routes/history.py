from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload
from auth import get_current_user
from contact_access import get_visible_contact
from database import get_db
from models import Employee, LeadStage
from models.activity_log import ActivityLog
from schemas.activity_log import HistoryItem

router = APIRouter(
    prefix="/contacts", tags=["history"], dependencies=[Depends(get_current_user)]
)


@router.get("/{contact_id}/history", response_model=list[HistoryItem], summary="Combined timeline of stage changes and activities")
def get_history(
    contact_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Returns stage changes and logged activities interleaved in reverse-chronological order."""
    get_visible_contact(db, current_user, contact_id)

    items: list[HistoryItem] = []

    # Stage changes
    stages = (
        db.query(LeadStage)
        .options(joinedload(LeadStage.stage_type))
        .filter(LeadStage.contact_id == contact_id)
        .all()
    )
    for s in stages:
        items.append(HistoryItem(
            event_type="stage",
            timestamp=s.changed_at,
            actor_id=s.changed_by,
            stage_slug=s.stage_type.slug if s.stage_type else None,
            stage_name=s.stage_type.name if s.stage_type else None,
            stage_note=s.loss_notes or s.awareness_note,
        ))

    # Activity logs
    logs = (
        db.query(ActivityLog)
        .options(joinedload(ActivityLog.doer))
        .filter(ActivityLog.contact_id == contact_id)
        .all()
    )
    for log in logs:
        items.append(HistoryItem(
            event_type="activity",
            timestamp=log.logged_at,
            actor_id=log.done_by,
            actor_name=log.doer.name if log.doer else None,
            activity_type=log.activity_type,
            outcome=log.outcome,
            notes=log.notes,
            followup_id=log.followup_id,
        ))

    items.sort(key=lambda x: x.timestamp, reverse=True)
    return items
