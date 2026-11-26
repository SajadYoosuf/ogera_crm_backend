"""
Contact interactions router — activity logs and follow-up tasks on contacts.

Mirrors lead_interactions.py with entity_type='contact'.

ENDPOINTS:
    GET    /contacts/{contact_id}/interactions                  — list (?kind=log|task)
    POST   /contacts/{contact_id}/interactions/log              — log an activity
    POST   /contacts/{contact_id}/interactions/task             — schedule a follow-up
    PATCH  /contact-interactions/{iid}                          — complete / change status
    PATCH  /contacts/{contact_id}/interactions/{iid}/reschedule — assignee or head
    PATCH  /contacts/{contact_id}/interactions/{iid}/reassign   — head only
    DELETE /contacts/{contact_id}/interactions/{iid}            — head only
"""
import datetime
import logging
from datetime import timezone as _tz
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth import get_current_user, get_tenant_db
from contact_lookup import get_live_contact
from models.employee import Employee
from models.interaction import Interaction
from services import lead_access
from services.notification_service import create_notification
from services.record_log_service import add_log

logger = logging.getLogger(__name__)

router = APIRouter(tags=["contact-interactions"], dependencies=[Depends(get_current_user)])

Outcome = Literal["done", "not_now", "no_answer"]
Priority = Literal["high", "medium", "low"]


def _now() -> datetime.datetime:
    return datetime.datetime.now(_tz.utc)


def _clean_type(v: str) -> str:
    v = v.strip().lower()
    if not v or len(v) > 50:
        raise ValueError("activity_type is required (50 characters max)")
    return v


class _ActivityFields(BaseModel):
    activity_type: str
    custom_label: str | None = Field(default=None, max_length=100)
    title: str | None = Field(default=None, max_length=255)

    @field_validator("activity_type")
    @classmethod
    def _type(cls, v: str) -> str:
        return _clean_type(v)

    @model_validator(mode="after")
    def _other_needs_label(self):
        if self.activity_type == "other" and not (self.custom_label or "").strip():
            raise ValueError("custom_label is required when activity_type is 'other'")
        return self


class LogCreate(_ActivityFields):
    notes: str = Field(min_length=1)
    outcome: Outcome | None = None
    done_by: int | None = None
    logged_at: datetime.datetime | None = None  # heads only

    @field_validator("notes")
    @classmethod
    def _notes(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Notes are required")
        return v


class TaskCreate(_ActivityFields):
    notes: str | None = None
    due_at: datetime.datetime
    assigned_to: int
    priority: Priority = "medium"


class TaskUpdate(BaseModel):
    status: Literal["todo", "inprogress", "completed"]
    outcome: Outcome | None = None
    notes: str | None = None
    actual_activity_type: str | None = None
    custom_label: str | None = None

    @model_validator(mode="after")
    def _completed_needs_outcome(self):
        if self.status == "completed" and self.outcome is None:
            raise ValueError("outcome is required when completing a task")
        return self


class RescheduleBody(BaseModel):
    due_at: datetime.datetime


class ReassignBody(BaseModel):
    assigned_to: int


_STATUS_LABELS = {"todo": "To do", "inprogress": "In progress", "completed": "Completed"}
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _ist_text(value: datetime.datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=_tz.utc)
    return aware.astimezone(_IST).strftime("%d %b %Y, %I:%M %p IST")


def _overdue_days(row: Interaction) -> int:
    if row.kind != "task" or row.status == "completed" or row.due_at is None:
        return 0
    due = row.due_at if row.due_at.tzinfo else row.due_at.replace(tzinfo=_tz.utc)
    return max(0, (_now() - due).days)


def _enrich_many(db: Session, rows: list[Interaction]) -> list[dict]:
    ids = {i for r in rows for i in (r.done_by, r.assigned_to, r.created_by) if i}
    names: dict[int, str] = {}
    if ids:
        names = {r.id: r.name for r in db.execute(select(Employee.id, Employee.name).where(Employee.id.in_(ids))).all()}
    out = []
    for r in rows:
        d = {c.name: getattr(r, c.name) for c in r.__table__.columns}
        d["logged_at"] = r.logged_at or r.created_at
        d["done_by_name"] = names.get(r.done_by)
        d["assigned_to_name"] = names.get(r.assigned_to)
        d["created_by_name"] = names.get(r.created_by)
        d["overdue_days"] = _overdue_days(r)
        out.append(d)
    return out


def _get_interaction(db: Session, iid: int, contact_id: int | None = None) -> Interaction:
    q = select(Interaction).where(Interaction.id == iid, Interaction.entity_type == "contact")
    if contact_id is not None:
        q = q.where(Interaction.entity_id == contact_id)
    row = db.scalar(q)
    if not row:
        raise HTTPException(404, detail="Interaction not found")
    return row


def _check_employee_exists(db: Session, employee_id: int) -> None:
    emp = db.scalar(select(Employee).where(Employee.id == employee_id))
    if emp is None or emp.deleted_at is not None or not emp.is_active:
        raise HTTPException(400, detail="That employee was not found or is inactive")


def _employee_name(db: Session, employee_id: int | None) -> str:
    employee = db.get(Employee, employee_id) if employee_id else None
    return employee.name if employee else "a team member"


def _notify_heads(
    db: Session, actor_id: int, contact_id: int, notif_type: str, title: str, message: str,
    background_tasks: BackgroundTasks | None = None, skip: set[int | None] = frozenset(),
) -> None:
    for head_id in lead_access.head_employee_ids(db):
        if head_id != actor_id and head_id not in skip:
            create_notification(db, head_id, notif_type, title, message, "contact", contact_id, background_tasks)


def _notify_task_parties(
    db: Session, actor: Employee, row: Interaction, notif_type: str, title: str, message: str,
    background_tasks: BackgroundTasks | None = None,
) -> None:
    if row.assigned_to and row.assigned_to != actor.id:
        create_notification(db, row.assigned_to, notif_type, title, message, "contact", row.entity_id, background_tasks)
    _notify_heads(db, actor.id, row.entity_id, notif_type, title, message, background_tasks, skip={row.assigned_to})


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/contacts/{contact_id}/interactions", summary="List interactions for a contact")
def list_interactions(
    contact_id: int,
    kind: Literal["log", "task"] | None = Query(None),
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    contact = get_live_contact(db, contact_id)
    lead_access.ensure_can_work_contact(current_user, contact)
    q = select(Interaction).where(Interaction.entity_type == "contact", Interaction.entity_id == contact_id)
    if kind:
        q = q.where(Interaction.kind == kind)
    rows = db.scalars(q.order_by(Interaction.created_at.desc(), Interaction.id.desc())).all()
    return _enrich_many(db, list(rows))


@router.post("/contacts/{contact_id}/interactions/log", status_code=201, summary="Log an activity on a contact")
def log_activity(
    contact_id: int,
    body: LogCreate,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    contact = get_live_contact(db, contact_id)
    lead_access.ensure_can_log_on_contact(current_user, contact)

    done_by = current_user.id
    if body.done_by is not None and body.done_by != current_user.id:
        lead_access.ensure_is_head(current_user, "log on behalf of another employee")
        _check_employee_exists(db, body.done_by)
        done_by = body.done_by
    if body.logged_at is not None:
        lead_access.ensure_is_head(current_user, "backdate activities")

    row = Interaction(
        entity_type="contact", entity_id=contact_id, kind="log",
        activity_type=body.activity_type,
        custom_label=body.custom_label.strip() if body.custom_label else None,
        title=body.title.strip() if body.title else None,
        notes=body.notes, outcome=body.outcome,
        done_by=done_by, created_by=current_user.id,
        logged_at=body.logged_at or _now(),
        status=None, priority=None,
    )
    db.add(row)
    add_log(db, "contact", contact_id, "activity_logged", current_user.id, new_value=body.custom_label or body.activity_type)
    db.commit()
    db.refresh(row)
    return _enrich_many(db, [row])[0]


@router.post("/contacts/{contact_id}/interactions/task", status_code=201, summary="Schedule a follow-up task on a contact")
def create_task(
    contact_id: int,
    body: TaskCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    contact = get_live_contact(db, contact_id)
    lead_access.ensure_can_work_contact(current_user, contact)
    lead_access.ensure_can_assign_task_on_contact(current_user, body.assigned_to)
    if body.assigned_to != current_user.id:
        _check_employee_exists(db, body.assigned_to)

    label = (body.custom_label or body.activity_type).replace("_", " ")
    row = Interaction(
        entity_type="contact", entity_id=contact_id, kind="task",
        activity_type=body.activity_type,
        custom_label=body.custom_label.strip() if body.custom_label else None,
        title=(body.title.strip() if body.title else None) or f"Follow-up: {label}",
        notes=body.notes, due_at=body.due_at, priority=body.priority, status="todo",
        assigned_to=body.assigned_to, created_by=current_user.id,
        logged_at=_now(),
    )
    db.add(row)
    db.flush()
    add_log(db, "contact", contact_id, "task_created", current_user.id, new_value=row.title)
    if body.assigned_to != current_user.id:
        create_notification(
            db, body.assigned_to, "followup_assigned", "New follow-up assigned",
            f"{current_user.name} assigned you: {row.title}", "contact", contact_id, background_tasks,
        )
    _notify_heads(
        db, current_user.id, contact_id, "followup_created", f"Follow-up created: {row.title}",
        f"{current_user.name} scheduled '{row.title}' for {_employee_name(db, body.assigned_to)} on contact #{contact_id}.",
        background_tasks, skip={body.assigned_to},
    )
    db.commit()
    db.refresh(row)
    return _enrich_many(db, [row])[0]


@router.patch("/contact-interactions/{iid}", summary="Change a contact task's status or complete it")
def update_task(
    iid: int,
    body: TaskUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    row = _get_interaction(db, iid)
    if row.kind != "task":
        raise HTTPException(400, detail="Only tasks can change status")
    lead_access.ensure_can_modify_interaction(current_user, row.assigned_to)

    old = row.status
    row.status = body.status
    if body.status == "completed":
        row.outcome = body.outcome
        if body.notes and body.notes.strip():
            row.notes = f"{row.notes}\n{body.notes.strip()}" if row.notes else body.notes.strip()
        add_log(db, "contact", row.entity_id, "task_completed", current_user.id, old, row.title)
        if old != "completed":
            done_type = body.actual_activity_type or row.activity_type
            done_label = (body.custom_label or "").strip() or None
            if done_type == "other" and not done_label:
                raise HTTPException(400, detail="Name how you contacted them")
            db.add(Interaction(
                entity_type="contact", entity_id=row.entity_id, kind="log",
                activity_type=done_type, custom_label=done_label if done_type == "other" else None,
                title=row.title, notes=(body.notes or "").strip() or None, outcome=body.outcome,
                done_by=current_user.id, created_by=current_user.id,
                parent_task_id=row.id, logged_at=_now(),
            ))
    else:
        add_log(db, "contact", row.entity_id, "task_status_changed", current_user.id, old, body.status)

    if old != body.status:
        label = _STATUS_LABELS[body.status]
        _notify_task_parties(
            db, current_user, row,
            "followup_completed" if body.status == "completed" else "followup_status",
            f"Follow-up {label.lower()}: {row.title}",
            f"{current_user.name} changed '{row.title}' to {label}.",
            background_tasks,
        )
    db.commit()
    db.refresh(row)
    return _enrich_many(db, [row])[0]


@router.patch("/contacts/{contact_id}/interactions/{iid}/reschedule", summary="Reschedule a contact task")
def reschedule_task(
    contact_id: int,
    iid: int,
    body: RescheduleBody,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    get_live_contact(db, contact_id)
    row = _get_interaction(db, iid, contact_id)
    if row.kind != "task":
        raise HTTPException(400, detail="Only tasks can be rescheduled")
    lead_access.ensure_can_modify_interaction(current_user, row.assigned_to)

    old_due = row.due_at
    row.due_at = body.due_at
    add_log(db, "contact", contact_id, "task_rescheduled", current_user.id, str(old_due), str(body.due_at))
    _notify_task_parties(
        db, current_user, row, "followup_rescheduled",
        f"Follow-up rescheduled: {row.title}",
        f"{current_user.name} moved '{row.title}' to {_ist_text(body.due_at)}.",
        background_tasks,
    )
    db.commit()
    db.refresh(row)
    return _enrich_many(db, [row])[0]


@router.patch("/contacts/{contact_id}/interactions/{iid}/reassign", summary="Reassign a contact task (admin/manager)")
def reassign_task(
    contact_id: int,
    iid: int,
    body: ReassignBody,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    lead_access.ensure_is_head(current_user, "reassign tasks")
    get_live_contact(db, contact_id)
    row = _get_interaction(db, iid, contact_id)
    if row.kind != "task":
        raise HTTPException(400, detail="Only tasks can be reassigned")
    _check_employee_exists(db, body.assigned_to)

    old = row.assigned_to
    row.assigned_to = body.assigned_to
    add_log(db, "contact", contact_id, "task_reassigned", current_user.id, old, body.assigned_to)
    new_name = _employee_name(db, body.assigned_to)
    summary = f"{current_user.name} reassigned '{row.title}' to {new_name}."
    if body.assigned_to != current_user.id:
        create_notification(
            db, body.assigned_to, "followup_assigned", "Follow-up assigned to you",
            f"{current_user.name} assigned you: {row.title}", "contact", contact_id, background_tasks,
        )
    if old and old not in (body.assigned_to, current_user.id):
        create_notification(
            db, old, "followup_reassigned", f"Follow-up reassigned: {row.title}", summary,
            "contact", contact_id, background_tasks,
        )
    _notify_heads(
        db, current_user.id, contact_id, "followup_reassigned", f"Follow-up reassigned: {row.title}", summary,
        background_tasks, skip={body.assigned_to, old},
    )
    db.commit()
    db.refresh(row)
    return _enrich_many(db, [row])[0]


@router.delete("/contacts/{contact_id}/interactions/{iid}", status_code=204, summary="Delete a contact interaction (admin/manager)")
def delete_interaction(
    contact_id: int,
    iid: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    lead_access.ensure_is_head(current_user, "delete interactions")
    get_live_contact(db, contact_id)
    row = _get_interaction(db, iid, contact_id)
    add_log(db, "contact", contact_id, f"{row.kind}_deleted", current_user.id, old_value=row.title or row.activity_type)
    if row.kind == "task":
        _notify_task_parties(
            db, current_user, row, "followup_deleted",
            f"Follow-up deleted: {row.title}",
            f"{current_user.name} deleted '{row.title}'.",
            background_tasks,
        )
    db.delete(row)
    db.commit()
