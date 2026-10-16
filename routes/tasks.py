import datetime
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session, joinedload
from auth import get_current_user
from database import get_db
from models import Contact, Employee, LeadActivity
from routes.lead_activities import activity_response, can_manage_tasks
from schemas.lead_activity import ActivityType, TaskListResponse, TaskResponse, TaskSummary

router = APIRouter(prefix="/tasks", tags=["tasks"], dependencies=[Depends(get_current_user)])

MAX_RANGE_DAYS = 62  # a month view with its leading and trailing days


@router.get("", response_model=TaskListResponse, summary="List follow-up tasks")
def list_tasks(
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    status: Literal["open", "done", "all", "due_today", "overdue"] = "open",
    assigned_to: int | None = None,
    type: ActivityType | None = None,
    contact_type: Literal["consumer", "partner"] | None = None,
    q: str | None = Query(default=None, max_length=100),
    include_overdue: bool = False,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Scheduled follow-ups across leads, for the all, day, week, month and custom views.
    Sales staff only get their own tasks. Admins and managers can filter by employee.
    `include_overdue` adds open tasks from before `date_from`, so the today list starts with what was missed. The date range can be at most 62 days.
    """
    if date_from and date_to and (date_to < date_from or (date_to - date_from).days > MAX_RANGE_DAYS):
        raise HTTPException(status_code=400, detail=f"date_to must be on or after date_from, within {MAX_RANGE_DAYS} days")

    if not can_manage_tasks(current_user):
        assigned_to = current_user.id

    # Everything except the date range and status: the summary counts share it
    scope = [
        LeadActivity.scheduled_date.is_not(None),
        Contact.deleted_at.is_(None),
    ]
    if assigned_to is not None:
        scope.append(LeadActivity.assigned_to == assigned_to)
    if type:
        scope.append(LeadActivity.type == type)
    if contact_type:
        scope.append(Contact.contact_type == contact_type)
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        scope.append(or_(Contact.name.ilike(like), Contact.phone.like(like)))

    now = datetime.datetime.now()
    today, clock = now.date(), now.time()
    pending = LeadActivity.status == "pending"
    overdue = and_(pending, or_(
        LeadActivity.scheduled_date < today,
        and_(LeadActivity.scheduled_date == today, LeadActivity.scheduled_time.is_not(None), LeadActivity.scheduled_time < clock),
    ))
    due_today = and_(pending, ~overdue, LeadActivity.scheduled_date == today)

    in_range = []
    if date_from:
        in_range.append(LeadActivity.scheduled_date >= date_from)
    if date_to:
        in_range.append(LeadActivity.scheduled_date <= date_to)

    window = and_(*in_range) if in_range else True
    if include_overdue and date_from and status != "done":
        window = or_(window, and_(LeadActivity.status == "pending", LeadActivity.scheduled_date < date_from))
    filters = [*scope, window]
    if status in ("overdue", "due_today"):
        # "Already late" and "due today" are about right now, so they ignore the date range
        filters = [*scope, overdue if status == "overdue" else due_today]
    elif status != "all":
        filters.append(LeadActivity.status == ("pending" if status == "open" else "done"))

    base = db.query(LeadActivity).join(Contact, Contact.id == LeadActivity.contact_id).filter(*filters)
    total = base.count()
    rows = (
        base.add_entity(Contact)
        .options(joinedload(LeadActivity.creator), joinedload(LeadActivity.assignee), joinedload(LeadActivity.completer))
        .order_by(
            LeadActivity.scheduled_date,
            LeadActivity.scheduled_time.is_(None),
            LeadActivity.scheduled_time,
            LeadActivity.id,
        )
        .offset(skip)
        .limit(limit)
        .all()
    )
    items = [
        TaskResponse(
            **activity_response(activity).model_dump(),
            contact_id=contact.id,
            contact_name=contact.name,
            contact_phone=contact.phone,
            contact_type=contact.contact_type,
        )
        for activity, contact in rows
    ]

    counts = (
        db.query(
            func.coalesce(func.sum(case((overdue, 1), else_=0)), 0),
            func.coalesce(func.sum(case((due_today, 1), else_=0)), 0),
            func.coalesce(func.sum(case((and_(pending, LeadActivity.scheduled_date > today), 1), else_=0)), 0),
            func.coalesce(func.sum(case((and_(LeadActivity.status == "done", window), 1), else_=0)), 0),
        )
        .join(Contact, Contact.id == LeadActivity.contact_id)
        .filter(*scope)
        .one()
    )
    summary = TaskSummary(overdue=int(counts[0]), today=int(counts[1]), upcoming=int(counts[2]), done=int(counts[3]))
    return TaskListResponse(items=items, total=total, summary=summary)
