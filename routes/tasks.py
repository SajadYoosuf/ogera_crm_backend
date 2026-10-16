import datetime
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session, joinedload
from auth import get_current_user
from database import get_db
from models import Contact, Employee
from models.followup_schedule import FollowupSchedule
from routes.followups import _followup_response, _task_state, TASK_MANAGER_ROLES
from schemas.followup_schedule import FollowupScheduleResponse

router = APIRouter(prefix="/tasks", tags=["tasks"], dependencies=[Depends(get_current_user)])

MAX_RANGE_DAYS = 62


class TaskSummary:
    overdue: int = 0
    today: int = 0
    upcoming: int = 0
    done: int = 0


from pydantic import BaseModel


class TaskSummaryResponse(BaseModel):
    overdue: int = 0
    today: int = 0
    upcoming: int = 0
    done: int = 0


class TaskListResponse(BaseModel):
    items: list[FollowupScheduleResponse]
    total: int
    summary: TaskSummaryResponse


@router.get("", response_model=TaskListResponse, summary="List follow-up tasks")
def list_tasks(
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    status: Literal["open", "done", "all", "due_today", "overdue"] = "open",
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

    if current_user.role not in TASK_MANAGER_ROLES:
        assigned_to = current_user.id

    now = datetime.datetime.now()
    today = now.date()

    scope = [Contact.deleted_at.is_(None)]
    if assigned_to is not None:
        scope.append(FollowupSchedule.assigned_to == assigned_to)
    if channel:
        scope.append(FollowupSchedule.channel == channel)
    if contact_type:
        scope.append(Contact.contact_type == contact_type)
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        scope.append(or_(Contact.name.ilike(like), Contact.phone.like(like)))

    base_q = (
        db.query(FollowupSchedule)
        .join(Contact, FollowupSchedule.contact_id == Contact.id)
        .options(joinedload(FollowupSchedule.contact), joinedload(FollowupSchedule.assignee))
        .filter(*scope)
    )

    # Summary counts (ignoring date range)
    pending_q = base_q.filter(FollowupSchedule.status == "pending")
    overdue_count = pending_q.filter(FollowupSchedule.due_at < now, func.date(FollowupSchedule.due_at) < today).count()
    today_count = pending_q.filter(func.date(FollowupSchedule.due_at) == today).count()
    upcoming_count = pending_q.filter(func.date(FollowupSchedule.due_at) > today).count()
    done_count = base_q.filter(FollowupSchedule.status == "done").count()
    summary = TaskSummaryResponse(overdue=overdue_count, today=today_count, upcoming=upcoming_count, done=done_count)

    # Filter by status
    if status == "open":
        filters = [FollowupSchedule.status == "pending"]
        if date_from:
            filters.append(func.date(FollowupSchedule.due_at) >= date_from)
            if include_overdue:
                filters = [FollowupSchedule.status == "pending", or_(
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
        items_q = base_q.filter(FollowupSchedule.status == "pending", func.date(FollowupSchedule.due_at) == today)
    elif status == "overdue":
        items_q = base_q.filter(FollowupSchedule.status == "pending", FollowupSchedule.due_at < now)
    elif status == "done":
        items_q = base_q.filter(FollowupSchedule.status == "done")
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
    return TaskListResponse(items=[_followup_response(r) for r in rows], total=total, summary=summary)
