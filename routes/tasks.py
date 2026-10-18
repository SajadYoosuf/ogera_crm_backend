import datetime
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, joinedload
from auth import get_current_user
from database import get_db
from models import Contact, Employee
from models.followup_schedule import FollowupSchedule
from contact_access import ensure_can_assign, visible_assignee_ids
from role_access import ensure_can_schedule_for
from routes.followups import _followup_response, _task_state, checked_channel, LEGACY_CHANNELS, MONITORING_ROLES
from schemas.followup_schedule import FollowupScheduleResponse, FollowupScheduleUpdate, Priority
from business_time import ist_now, today_ist

router = APIRouter(prefix="/tasks", tags=["tasks"], dependencies=[Depends(get_current_user)])

MAX_RANGE_DAYS = 62


class TaskSummaryResponse(BaseModel):
    overdue: int = 0
    today: int = 0
    upcoming: int = 0
    done: int = 0


class TaskItemResponse(BaseModel):
    """One task as API.md describes GET /tasks: the activity fields the Tasks screen reads, plus its contact."""
    id: int
    title: str | None = None
    type: str
    notes: str | None = None
    status: Literal["pending", "done"]
    priority: str = "medium"
    scheduled_date: str
    scheduled_time: str
    assigned_to: int
    assigned_to_name: str | None = None
    completed_at: datetime.datetime | None = None
    completed_by: int | None = None
    completed_by_name: str | None = None
    coordinates: dict | None = None
    task_id: int
    created_by: int
    created_by_name: str | None = None
    created_at: datetime.datetime
    task_state: Literal["pending", "due_today", "overdue", "done"]
    overdue_days: int = 0
    contact_id: int | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_type: str | None = None


class TaskListResponse(BaseModel):
    items: list[TaskItemResponse]
    total: int
    summary: TaskSummaryResponse


def _task_item(fs: FollowupSchedule) -> TaskItemResponse:
    base = _followup_response(fs)
    done = fs.status == "completed"
    # The stored channel is the short legacy name for some types ("call" is phone_call); the API speaks activity types.
    channel = fs.channel
    return TaskItemResponse(
        id=fs.id,
        title=fs.title,
        type=LEGACY_CHANNELS.get(channel, channel) if channel else "other",
        notes=fs.notes,
        status="done" if done else "pending",
        priority=fs.priority,
        scheduled_date=fs.due_at.date().isoformat(),
        scheduled_time=fs.due_at.strftime("%H:%M:%S"),
        assigned_to=fs.assigned_to,
        assigned_to_name=base.assigned_to_name,
        task_id=fs.id,
        created_by=fs.assigned_to,
        created_by_name=base.assigned_to_name,
        created_at=fs.created_at,
        task_state="done" if done else ("pending" if base.task_state in ("todo", "inprogress") else base.task_state),
        overdue_days=base.overdue_days,
        contact_id=fs.contact_id,
        contact_name=base.contact_name,
        contact_phone=base.contact_phone,
        contact_type=base.contact_type,
    )


class StandaloneTaskCreate(BaseModel):
    title: str
    due_at: datetime.datetime
    assigned_to: int
    priority: Priority = "medium"
    channel: str | None = Field(default=None, min_length=1, max_length=15)
    notes: str | None = None

    @model_validator(mode="after")
    def validate_due_at(self) -> "StandaloneTaskCreate":
        if self.due_at.replace(tzinfo=None) < ist_now() - datetime.timedelta(minutes=5):
            raise ValueError("due_at cannot be in the past")
        return self


@router.get("", response_model=TaskListResponse, summary="List follow-up tasks")
def list_tasks(
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    status: Literal["open", "todo", "inprogress", "completed", "all", "due_today", "overdue"] = "open",
    assigned_to: int | None = None,
    channel: str | None = None,
    contact_type: Literal["consumer", "partner", "dealer"] | None = None,
    q: str | None = Query(default=None, max_length=100),
    include_overdue: bool = False,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    if date_from and date_to and (date_to < date_from or (date_to - date_from).days > MAX_RANGE_DAYS):
        raise HTTPException(status_code=400, detail=f"date_to must be on or after date_from, within {MAX_RANGE_DAYS} days")

    today = today_ist()

    scope = []
    visible = visible_assignee_ids(db, current_user)
    if visible is not None:
        scope.append(FollowupSchedule.assigned_to.in_(visible))
    if assigned_to is not None:
        scope.append(FollowupSchedule.assigned_to == assigned_to)
    if channel:
        scope.append(FollowupSchedule.channel == channel)
    if contact_type:
        scope.append(Contact.contact_type == contact_type)
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        scope.append(or_(
            FollowupSchedule.title.ilike(like),
            Contact.name.ilike(like),
            Contact.phone.like(like),
        ))

    base_q = (
        db.query(FollowupSchedule)
        .outerjoin(Contact, FollowupSchedule.contact_id == Contact.id)
        .options(joinedload(FollowupSchedule.contact), joinedload(FollowupSchedule.assignee))
        .filter(
            or_(FollowupSchedule.contact_id.is_(None), Contact.deleted_at.is_(None)),
            *scope,
        )
    )

    open_statuses = ("todo", "inprogress")

    # Summary counts (ignoring date range)
    open_q = base_q.filter(FollowupSchedule.status.in_(open_statuses))
    overdue_count = open_q.filter(func.date(FollowupSchedule.due_at) < today).count()
    today_count = open_q.filter(func.date(FollowupSchedule.due_at) == today).count()
    upcoming_count = open_q.filter(func.date(FollowupSchedule.due_at) > today).count()
    done_count = base_q.filter(FollowupSchedule.status == "completed").count()
    summary = TaskSummaryResponse(overdue=overdue_count, today=today_count, upcoming=upcoming_count, done=done_count)

    # Filter by status
    if status in ("open", "todo", "inprogress"):
        statuses = open_statuses if status == "open" else (status,)
        filters = [FollowupSchedule.status.in_(statuses)]
        if date_from:
            filters.append(func.date(FollowupSchedule.due_at) >= date_from)
            if include_overdue:
                filters = [FollowupSchedule.status.in_(statuses), or_(
                    func.date(FollowupSchedule.due_at) < date_from,
                    and_(
                        func.date(FollowupSchedule.due_at) >= date_from,
                        func.date(FollowupSchedule.due_at) <= (date_to or date_from),
                    ),
                )]
        if date_to and not include_overdue:
            filters.append(func.date(FollowupSchedule.due_at) <= date_to)
        items_q = base_q.filter(*filters)
    elif status == "due_today":
        items_q = base_q.filter(FollowupSchedule.status.in_(open_statuses), func.date(FollowupSchedule.due_at) == today)
    elif status == "overdue":
        items_q = base_q.filter(FollowupSchedule.status.in_(open_statuses), func.date(FollowupSchedule.due_at) < today)
    elif status == "completed":
        items_q = base_q.filter(FollowupSchedule.status == "completed")
        if date_from:
            items_q = items_q.filter(func.date(FollowupSchedule.due_at) >= date_from)
        if date_to:
            items_q = items_q.filter(func.date(FollowupSchedule.due_at) <= date_to)
    else:  # all
        items_q = base_q
        if date_from:
            items_q = items_q.filter(func.date(FollowupSchedule.due_at) >= date_from)
        if date_to:
            items_q = items_q.filter(func.date(FollowupSchedule.due_at) <= date_to)

    total = items_q.count()
    rows = items_q.order_by(FollowupSchedule.due_at.asc()).offset(skip).limit(limit).all()
    return TaskListResponse(items=[_task_item(r) for r in rows], total=total, summary=summary)


@router.post("", response_model=FollowupScheduleResponse, status_code=201, summary="Create a standalone task (not linked to a contact)")
def create_task(
    body: StandaloneTaskCreate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    assignee = db.query(Employee).filter(Employee.id == body.assigned_to, Employee.deleted_at.is_(None), Employee.is_active == 1).first()
    if not assignee:
        raise HTTPException(status_code=400, detail="Assigned employee not found or inactive")
    if assignee.role in MONITORING_ROLES:
        raise HTTPException(status_code=400, detail="Tasks cannot be assigned to an admin or manager")
    ensure_can_schedule_for(db, current_user, body.assigned_to)
    ensure_can_assign(db, current_user, body.assigned_to)

    fs = FollowupSchedule(
        contact_id=None,
        assigned_to=body.assigned_to,
        title=body.title.strip() or None,
        due_at=body.due_at,
        status="todo",
        priority=body.priority,
        channel=checked_channel(db, body.channel),
        notes=(body.notes or "").strip() or None,
    )
    db.add(fs)
    db.commit()
    db.refresh(fs)
    db.refresh(fs, attribute_names=["assignee"])
    return _followup_response(fs)


@router.patch("/{task_id}", response_model=FollowupScheduleResponse, summary="Update a task (status, reschedule, priority)")
def update_task(
    task_id: int,
    body: FollowupScheduleUpdate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Works for both standalone tasks and contact-linked follow-ups by task id."""
    fs = db.query(FollowupSchedule).filter(FollowupSchedule.id == task_id).first()
    if not fs:
        raise HTTPException(status_code=404, detail="Task not found")
    visible = visible_assignee_ids(db, current_user)
    if visible is not None and fs.assigned_to not in visible:
        raise HTTPException(status_code=403, detail="This task is assigned to someone else")
    if fs.status == "completed":
        raise HTTPException(status_code=409, detail="Task is already completed")

    if body.status == "completed":
        fs.status = "completed"
    elif body.status in ("todo", "inprogress"):
        fs.status = body.status
    else:  # reschedule
        fs.due_at = body.due_at

    db.commit()
    db.refresh(fs)
    db.refresh(fs, attribute_names=["contact", "assignee"])
    return _followup_response(fs)
