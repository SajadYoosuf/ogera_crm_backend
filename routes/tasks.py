import datetime
from typing import Literal
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload
from auth import get_current_user
from database import get_db
from models import Contact, Employee
from models.followup_schedule import FollowupSchedule
from models.activity_log import ActivityLog
from models.followup_edit_log import FollowupEditLog
from routes.followups import _followup_response, _log_edit, _task_state, checked_channel, LEGACY_CHANNELS
from schemas.followup_schedule import FollowupScheduleResponse, FollowupScheduleUpdate, Priority
from business_time import ist_day_bounds, ist_now, today_ist
from services.lead_access import ensure_can_assign_followup, ensure_can_work_followup

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
    # Legacy UI status ("pending" = still open, "done" = completed). Kept so the mobile app keeps working.
    status: Literal["pending", "done"]
    # The real stored status. The web dropdown binds to this so it shows todo / inprogress / completed.
    followup_status: str
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
        followup_status=fs.status,
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
    day_start, next_day = ist_day_bounds(today)

    # Summary counts (ignoring date range)
    open_q = base_q.filter(FollowupSchedule.status.in_(open_statuses))
    overdue_count = open_q.filter(FollowupSchedule.due_at < day_start).count()
    today_count = open_q.filter(
        FollowupSchedule.due_at >= day_start,
        FollowupSchedule.due_at < next_day,
    ).count()
    upcoming_count = open_q.filter(FollowupSchedule.due_at >= next_day).count()
    done_count = base_q.filter(FollowupSchedule.status == "completed").count()
    summary = TaskSummaryResponse(overdue=overdue_count, today=today_count, upcoming=upcoming_count, done=done_count)

    # Filter by status
    if status in ("open", "todo", "inprogress"):
        statuses = open_statuses if status == "open" else (status,)
        filters = [FollowupSchedule.status.in_(statuses)]
        if date_from:
            if include_overdue:
                _, include_end = ist_day_bounds(date_to or date_from)
                filters.append(FollowupSchedule.due_at < include_end)
            else:
                start_at, _ = ist_day_bounds(date_from)
                filters.append(FollowupSchedule.due_at >= start_at)
        if date_to and not include_overdue:
            _, end_at = ist_day_bounds(date_to)
            filters.append(FollowupSchedule.due_at < end_at)
        items_q = base_q.filter(*filters)
    elif status == "due_today":
        items_q = base_q.filter(
            FollowupSchedule.status.in_(open_statuses),
            FollowupSchedule.due_at >= day_start,
            FollowupSchedule.due_at < next_day,
        )
    elif status == "overdue":
        items_q = base_q.filter(
            FollowupSchedule.status.in_(open_statuses),
            FollowupSchedule.due_at < day_start,
        )
    elif status == "completed":
        items_q = base_q.filter(FollowupSchedule.status == "completed")
        if date_from:
            start_at, _ = ist_day_bounds(date_from)
            items_q = items_q.filter(FollowupSchedule.due_at >= start_at)
        if date_to:
            _, end_at = ist_day_bounds(date_to)
            items_q = items_q.filter(FollowupSchedule.due_at < end_at)
    else:  # all
        items_q = base_q
        if date_from:
            start_at, _ = ist_day_bounds(date_from)
            items_q = items_q.filter(FollowupSchedule.due_at >= start_at)
        if date_to:
            _, end_at = ist_day_bounds(date_to)
            items_q = items_q.filter(FollowupSchedule.due_at < end_at)

    total = items_q.count()
    rows = items_q.order_by(FollowupSchedule.due_at.asc()).offset(skip).limit(limit).all()
    return TaskListResponse(items=[_task_item(r) for r in rows], total=total, summary=summary)


@router.post("", response_model=FollowupScheduleResponse, status_code=201, summary="Create a standalone task (not linked to a contact)")
def create_task(
    body: StandaloneTaskCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    ensure_can_assign_followup(current_user, body.assigned_to)
    assignee = db.query(Employee).filter(Employee.id == body.assigned_to, Employee.deleted_at.is_(None), Employee.is_active == 1).first()
    if not assignee:
        raise HTTPException(status_code=400, detail="Assigned employee not found or inactive")

    fs = FollowupSchedule(
        contact_id=None,
        assigned_to=body.assigned_to,
        title=body.title.strip() or None,
        due_at=body.due_at,
        status="todo",
        priority=body.priority,
        channel=checked_channel(db, body.channel),
        notes=(body.notes or "").strip() or None,
        created_by=current_user.id,
    )
    db.add(fs)
    db.commit()
    db.refresh(fs)
    db.refresh(fs, attribute_names=["assignee"])

    # Notify the assigned employee and admin/manager about the new task
    try:
        from services.notification_service import create_notification, notify_roles
        task_title = fs.title or "Task"
        due_str = fs.due_at.strftime("%d %b %Y")
        title = f"New task: {task_title}"
        msg = f'A task "{task_title}" has been assigned to you, due {due_str}.'
        if body.assigned_to != current_user.id:
            create_notification(db, body.assigned_to, "task_created", title, msg, "task", fs.id, background_tasks)
        notify_roles(db, ["admin", "manager"], "task_created", title, msg, "task", fs.id, background_tasks)
        db.commit()
    except Exception:
        pass

    return _followup_response(fs)


@router.patch("/{task_id}", response_model=FollowupScheduleResponse, summary="Update a task (status, reschedule, priority)")
def update_task(
    task_id: int,
    body: FollowupScheduleUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Works for both standalone tasks and contact-linked follow-ups by task id.

    Completing a contact-linked task writes an activity_log row so the contact's timeline
    stays consistent whether the task was completed from the Tasks screen or the Lead Panel.
    """
    fs = db.query(FollowupSchedule).options(
        joinedload(FollowupSchedule.contact), joinedload(FollowupSchedule.assignee)
    ).filter(FollowupSchedule.id == task_id).first()
    if not fs:
        raise HTTPException(status_code=404, detail="Task not found")
    ensure_can_work_followup(current_user, fs)
    if fs.status == "completed":
        raise HTTPException(status_code=409, detail="Task is already completed")

    if body.status == "completed":
        actual_ch = body.actual_channel or fs.channel
        logged_type = LEGACY_CHANNELS.get(actual_ch or "", actual_ch or "other")
        if body.actual_channel and body.actual_channel != fs.channel:
            _log_edit(db, fs, current_user.id, "channel", fs.channel, body.actual_channel)

        fs.status = "completed"
        _log_edit(db, fs, current_user.id, "status", "open", "completed")

        # Bug 1 fix: write activity_log when completing a contact-linked task via the global
        # /tasks endpoint. Previously this was only done in /contacts/{id}/followups/{id}.
        if fs.contact_id:
            db.add(ActivityLog(
                contact_id=fs.contact_id,
                done_by=current_user.id,
                activity_type=logged_type,
                outcome=body.outcome,
                notes=(body.notes or "").strip() or None,
                followup_id=fs.id,
            ))

    elif body.status in ("todo", "inprogress"):
        _log_edit(db, fs, current_user.id, "status", fs.status, body.status)
        fs.status = body.status

    else:  # reschedule
        _log_edit(db, fs, current_user.id, "due_at", fs.due_at.isoformat(), body.due_at.isoformat())
        fs.due_at = body.due_at

    db.commit()
    db.refresh(fs)
    db.refresh(fs, attribute_names=["contact", "assignee"])
    if body.status == "completed":
        try:
            from services.notification_service import notify_task_completed
            notify_task_completed(db, fs, current_user, background_tasks)
            db.commit()
        except Exception:
            pass
    return _followup_response(fs)
