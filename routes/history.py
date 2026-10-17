from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload
from auth import get_current_user
from contact_access import get_visible_contact
from database import get_db
from models import ContactAssignment, Employee, FollowupReschedule, FollowupSchedule, LeadStage
from models.activity_log import ActivityLog
from schemas.activity_log import HistoryItem

router = APIRouter(
    prefix="/contacts", tags=["history"], dependencies=[Depends(get_current_user)]
)


@router.get("/{contact_id}/history", response_model=list[HistoryItem], summary="Combined timeline: stages, follow-ups, activities and assignments")
def get_history(
    contact_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Every stage move, scheduled follow-up, logged activity and owner change for one contact, newest first."""
    contact = get_visible_contact(db, current_user, contact_id)

    items: list[HistoryItem] = []

    # Stage changes, oldest first so each one knows the stage it moved from
    stages = (
        db.query(LeadStage)
        .options(joinedload(LeadStage.stage_type), joinedload(LeadStage.changed_by_employee))
        .filter(LeadStage.contact_id == contact_id)
        .order_by(LeadStage.changed_at, LeadStage.id)
        .all()
    )
    previous_name = None
    for s in stages:
        name = s.stage_type.name if s.stage_type else None
        items.append(HistoryItem(
            event_type="stage",
            timestamp=s.changed_at,
            actor_id=s.changed_by,
            actor_name=s.changed_by_employee.name if s.changed_by_employee else None,
            stage_slug=s.stage_type.slug if s.stage_type else None,
            stage_name=name,
            previous_stage_name=previous_name,
            stage_note=s.loss_notes or s.awareness_note,
        ))
        previous_name = name

    # Logged activities (calls, visits, notes, and follow-up completions)
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
            title=log.title,
            outcome=log.outcome,
            notes=log.notes,
            followup_id=log.followup_id,
        ))

    # Follow-ups as they were scheduled
    followups = (
        db.query(FollowupSchedule)
        .options(joinedload(FollowupSchedule.assignee))
        .filter(FollowupSchedule.contact_id == contact_id)
        .all()
    )
    for f in followups:
        items.append(HistoryItem(
            event_type="followup",
            timestamp=f.created_at,
            followup_id=f.id,
            title=f.title,
            channel=f.channel,
            due_at=f.due_at,
            followup_status=f.status,
            assigned_to_name=f.assignee.name if f.assignee else None,
            notes=f.notes,
        ))

    # Follow-up reschedules: each move of a due date, with the date it came from
    reschedules = (
        db.query(FollowupReschedule)
        .options(joinedload(FollowupReschedule.followup), joinedload(FollowupReschedule.changer))
        .filter(FollowupReschedule.contact_id == contact_id)
        .all()
    )
    for r in reschedules:
        items.append(HistoryItem(
            event_type="followup_rescheduled",
            timestamp=r.changed_at,
            actor_id=r.changed_by,
            actor_name=r.changer.name if r.changer else None,
            followup_id=r.followup_id,
            title=r.followup.title if r.followup else None,
            channel=r.followup.channel if r.followup else None,
            previous_due_at=r.from_due_at,
            due_at=r.to_due_at,
            followup_status="rescheduled",
        ))

    # Owner changes. Contacts from before this log existed show their current owner at creation.
    assignments = (
        db.query(ContactAssignment)
        .options(
            joinedload(ContactAssignment.from_employee),
            joinedload(ContactAssignment.to_employee),
            joinedload(ContactAssignment.changer),
        )
        .filter(ContactAssignment.contact_id == contact_id)
        .all()
    )
    for a in assignments:
        items.append(HistoryItem(
            event_type="assignment",
            timestamp=a.changed_at,
            actor_id=a.changed_by,
            actor_name=a.changer.name if a.changer else None,
            assigned_to_name=a.to_employee.name if a.to_employee else None,
            assigned_from_name=a.from_employee.name if a.from_employee else None,
        ))
    if not assignments and contact.assigned_employee:
        items.append(HistoryItem(
            event_type="assignment",
            timestamp=contact.created_at,
            assigned_to_name=contact.assigned_employee.name,
        ))

    items.sort(key=lambda x: x.timestamp, reverse=True)
    return items
