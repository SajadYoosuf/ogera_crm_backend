"""
Lead interactions router — activity logs and follow-up tasks on leads.

Rows live in the `interactions` table (entity_type='lead'). Every change is also
written to `record_logs` against the parent lead.

The request/response shapes match the web app's lib/api/interactions.ts:
    status   : todo | inprogress | completed
    priority : high | medium | low
    outcome  : done | not_now | no_answer
    activity_type 'other' carries a free-text custom_label.

ENDPOINTS:
    GET    /leads/{lead_id}/interactions                    — list (optional ?kind=log|task)
    POST   /leads/{lead_id}/interactions/log                — log an activity
    POST   /leads/{lead_id}/interactions/task               — schedule a follow-up task
    PATCH  /interactions/{iid}                              — change status / complete (assignee or head)
    PATCH  /leads/{lead_id}/interactions/{iid}/reschedule   — assignee or head
    PATCH  /leads/{lead_id}/interactions/{iid}/reassign     — head only
    DELETE /leads/{lead_id}/interactions/{iid}              — head only

RULES (services/lead_access.py):
    Staff may only work on leads assigned to them, may schedule tasks for
    themselves only, and may change only tasks assigned to them.
"""
import datetime
import logging
from datetime import timezone as _tz
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth import get_current_user, get_tenant_db
from models.employee import Employee
from models.interaction import Interaction
from models.lead import Lead
import services.lead_service as lead_svc
from services import lead_access
from services.notification_service import create_notification
from services.record_log_service import add_log

logger = logging.getLogger(__name__)

router = APIRouter(tags=["lead-interactions"], dependencies=[Depends(get_current_user)])

Outcome = Literal["done", "not_now", "no_answer"]
Priority = Literal["high", "medium", "low"]


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
    done_by: int | None = None                  # heads only
    logged_at: datetime.datetime | None = None  # heads only (historical logs)

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

    @model_validator(mode="after")
    def _completed_needs_outcome(self):
        if self.status == "completed" and self.outcome is None:
            raise ValueError("outcome is required when completing a task")
        return self


class RescheduleBody(BaseModel):
    due_at: datetime.datetime


class ReassignBody(BaseModel):
    assigned_to: int


def _now() -> datetime.datetime:
    return datetime.datetime.now(_tz.utc)


def _overdue_days(row: Interaction) -> int:
    if row.kind != "task" or row.status == "completed" or row.due_at is None:
        return 0
    due = row.due_at if row.due_at.tzinfo else row.due_at.replace(tzinfo=_tz.utc)
    return max(0, (_now() - due).days)


def _get_interaction(db: Session, iid: int, lead_id: int | None = None) -> Interaction:
    q = select(Interaction).where(Interaction.id == iid, Interaction.entity_type == "lead")
    if lead_id is not None:
        q = q.where(Interaction.entity_id == lead_id)
    row = db.scalar(q)
    if not row:
        raise HTTPException(404, detail="Interaction not found")
    return row


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


def _check_employee_exists(db: Session, employee_id: int) -> None:
    emp = db.scalar(select(Employee).where(Employee.id == employee_id))
    if emp is None or emp.deleted_at is not None or not emp.is_active:
        raise HTTPException(400, detail="That employee was not found or is inactive")


def _lead_for(db: Session, lead_id: int) -> Lead:
    return lead_svc.get_lead(db, lead_id)


@router.get("/leads/{lead_id}/interactions", summary="List interactions for a lead")
def list_interactions(
    lead_id: int,
    kind: Literal["log", "task"] | None = Query(None),
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    lead_access.ensure_can_work_lead(current_user, _lead_for(db, lead_id))
    q = select(Interaction).where(Interaction.entity_type == "lead", Interaction.entity_id == lead_id)
    if kind:
        q = q.where(Interaction.kind == kind)
    rows = db.scalars(q.order_by(Interaction.created_at.desc(), Interaction.id.desc())).all()
    return _enrich_many(db, list(rows))


_TIMELINE_LABELS = {
    "created": "Lead created",
    "edited": "Lead edited",
    "status_changed": "Status changed",
    "assigned": "Lead assigned",
    "converted": "Converted to contact",
    "activity_logged": "Activity logged",
    "task_created": "Follow-up scheduled",
    "task_completed": "Follow-up completed",
    "task_status_changed": "Follow-up status changed",
    "task_rescheduled": "Follow-up rescheduled",
    "task_reassigned": "Follow-up reassigned",
    "task_deleted": "Follow-up deleted",
    "log_deleted": "Activity deleted",
}


@router.get("/leads/{lead_id}/timeline", summary="History of a lead, newest first")
def lead_timeline(
    lead_id: int,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    from models.record_log import RecordLog

    lead_access.ensure_can_work_lead(current_user, _lead_for(db, lead_id))
    rows = db.scalars(
        select(RecordLog)
        .where(RecordLog.entity_type == "lead", RecordLog.entity_id == lead_id)
        .order_by(RecordLog.created_at.desc(), RecordLog.id.desc())
    ).all()
    # Assignment rows store the employee id in new_value; resolve it to a name for the detail line.
    assignee_actions = {"assigned", "task_reassigned"}
    ids = {r.actor_id for r in rows if r.actor_id}
    ids |= {int(r.new_value) for r in rows if r.action in assignee_actions and r.new_value and r.new_value.isdigit()}
    names = {}
    if ids:
        names = {r.id: r.name for r in db.execute(select(Employee.id, Employee.name).where(Employee.id.in_(ids))).all()}

    def _detail(r: RecordLog) -> str | None:
        if r.action not in assignee_actions:
            return None
        if not r.new_value:
            return "Unassigned"
        assignee = names.get(int(r.new_value)) if r.new_value.isdigit() else None
        return f"Assigned to {assignee}" if assignee else "Assigned to an employee"

    return [
        {
            "id": r.id,
            "action": r.action,
            "label": _TIMELINE_LABELS.get(r.action, r.action.replace("_", " ").capitalize()),
            "detail": _detail(r),
            "actor_name": names.get(r.actor_id),
            "old_value": r.old_value,
            "new_value": r.new_value,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.post("/leads/{lead_id}/interactions/log", status_code=201, summary="Log an activity on a lead")
def log_activity(
    lead_id: int,
    body: LogCreate,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    lead_access.ensure_can_log_on_lead(current_user, _lead_for(db, lead_id))
    done_by = current_user.id
    if body.done_by is not None and body.done_by != current_user.id:
        lead_access.ensure_is_head(current_user, "log on behalf of another employee")
        _check_employee_exists(db, body.done_by)
        done_by = body.done_by
    if body.logged_at is not None:
        lead_access.ensure_is_head(current_user, "backdate activities")

    row = Interaction(
        entity_type="lead", entity_id=lead_id, kind="log",
        activity_type=body.activity_type,
        custom_label=body.custom_label.strip() if body.custom_label else None,
        title=body.title.strip() if body.title else None,
        notes=body.notes, outcome=body.outcome,
        done_by=done_by, created_by=current_user.id,
        logged_at=body.logged_at or _now(),
        status=None, priority=None,
    )
    db.add(row)
    add_log(db, "lead", lead_id, "activity_logged", current_user.id, new_value=body.custom_label or body.activity_type)
    db.commit()
    db.refresh(row)
    return _enrich_many(db, [row])[0]


@router.post("/leads/{lead_id}/interactions/task", status_code=201, summary="Schedule a follow-up task on a lead")
def create_task(
    lead_id: int,
    body: TaskCreate,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    lead_access.ensure_can_work_lead(current_user, _lead_for(db, lead_id))
    lead_access.ensure_can_assign_task(current_user, body.assigned_to)
    if body.assigned_to != current_user.id:
        _check_employee_exists(db, body.assigned_to)

    label = (body.custom_label or body.activity_type).replace("_", " ")
    row = Interaction(
        entity_type="lead", entity_id=lead_id, kind="task",
        activity_type=body.activity_type,
        custom_label=body.custom_label.strip() if body.custom_label else None,
        title=(body.title.strip() if body.title else None) or f"Follow-up: {label}",
        notes=body.notes, due_at=body.due_at, priority=body.priority, status="todo",
        assigned_to=body.assigned_to, created_by=current_user.id,
    )
    db.add(row)
    db.flush()
    add_log(db, "lead", lead_id, "task_created", current_user.id, new_value=row.title)
    if body.assigned_to != current_user.id:
        create_notification(
            db, body.assigned_to, "followup_assigned", "New follow-up assigned",
            f"{current_user.name} assigned you: {row.title}", "lead", lead_id,
        )
    db.commit()
    db.refresh(row)
    return _enrich_many(db, [row])[0]


@router.patch("/interactions/{iid}", summary="Change a task's status or complete it")
def update_task(
    iid: int,
    body: TaskUpdate,
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
        add_log(db, "lead", row.entity_id, "task_completed", current_user.id, old, row.title)
    else:
        add_log(db, "lead", row.entity_id, "task_status_changed", current_user.id, old, body.status)
    db.commit()
    db.refresh(row)
    return _enrich_many(db, [row])[0]


@router.patch("/leads/{lead_id}/interactions/{iid}/reschedule", summary="Reschedule a task")
def reschedule_task(
    lead_id: int,
    iid: int,
    body: RescheduleBody,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    _lead_for(db, lead_id)
    row = _get_interaction(db, iid, lead_id)
    if row.kind != "task":
        raise HTTPException(400, detail="Only tasks can be rescheduled")
    lead_access.ensure_can_modify_interaction(current_user, row.assigned_to)
    old_due = row.due_at
    row.due_at = body.due_at
    add_log(db, "lead", lead_id, "task_rescheduled", current_user.id, str(old_due), str(body.due_at))
    db.commit()
    db.refresh(row)
    return _enrich_many(db, [row])[0]


@router.patch("/leads/{lead_id}/interactions/{iid}/reassign", summary="Reassign a task (admin/manager)")
def reassign_interaction(
    lead_id: int,
    iid: int,
    body: ReassignBody,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    lead_access.ensure_is_head(current_user, "reassign tasks")
    _lead_for(db, lead_id)
    row = _get_interaction(db, iid, lead_id)
    if row.kind != "task":
        raise HTTPException(400, detail="Only tasks can be reassigned")
    _check_employee_exists(db, body.assigned_to)
    old = row.assigned_to
    row.assigned_to = body.assigned_to
    add_log(db, "lead", lead_id, "task_reassigned", current_user.id, old, body.assigned_to)
    if body.assigned_to != current_user.id:
        create_notification(
            db, body.assigned_to, "followup_assigned", "Follow-up assigned to you",
            f"{current_user.name} assigned you: {row.title}", "lead", lead_id,
        )
    db.commit()
    db.refresh(row)
    return _enrich_many(db, [row])[0]


@router.delete("/leads/{lead_id}/interactions/{iid}", status_code=204, summary="Delete an interaction (admin/manager)")
def delete_interaction(
    lead_id: int,
    iid: int,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    lead_access.ensure_is_head(current_user, "delete interactions")
    _lead_for(db, lead_id)
    row = _get_interaction(db, iid, lead_id)
    add_log(db, "lead", lead_id, f"{row.kind}_deleted", current_user.id, old_value=row.title or row.activity_type)
    db.delete(row)
    db.commit()
