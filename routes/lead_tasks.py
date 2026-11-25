"""
Lead tasks — every follow-up (interactions with kind='task' on leads) in one list, for the Tasks page.

ENDPOINT:
    GET /lead-tasks?bucket=today|overdue|upcoming|completed&assigned_to=&q=&skip=&limit=

BUCKETS (IST calendar days, business_time.py):
    today      open and due within today (includes tasks already late today)
    overdue    open and due_at < now
    upcoming   open and due after today (or with no due time)
    completed  completed, newest first

ACCESS:
    Staff always see only their own tasks (assigned_to is forced to them).
    Admin and manager (see_all_records) see everyone's, optionally filtered by assigned_to.

`/tasks` is a legacy contact endpoint, hence the different path.
"""
import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from auth import get_current_user, get_tenant_db
from business_time import ist_day_bounds, today_ist
from models.employee import Employee
from models.interaction import Interaction
from models.lead import Lead
from routes.lead_interactions import _enrich_many
from services import lead_access

router = APIRouter(tags=["lead-tasks"], dependencies=[Depends(get_current_user)])

Bucket = Literal["today", "overdue", "upcoming", "completed"]
BUCKETS: tuple[str, ...] = ("today", "overdue", "upcoming", "completed")


def _db_time(db: Session, value: datetime.datetime) -> datetime.datetime:
    """A naive-UTC instant in the form the database compares correctly: aware for PostgreSQL, naive for SQLite."""
    if db.get_bind().dialect.name == "sqlite":
        return value
    return value.replace(tzinfo=datetime.timezone.utc)


def _conditions(db: Session) -> dict[str, object]:
    now = _db_time(db, datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None))
    start, end = (_db_time(db, t) for t in ist_day_bounds(today_ist()))
    open_ = Interaction.status != "completed"
    return {
        "today": and_(open_, Interaction.due_at >= start, Interaction.due_at < end),
        "overdue": and_(open_, Interaction.due_at < now),
        "upcoming": and_(open_, or_(Interaction.due_at >= end, Interaction.due_at.is_(None))),
        "completed": Interaction.status == "completed",
    }


def _base_filters(current_user: Employee, assigned_to: int | None, q: str | None) -> list:
    filters = [
        Interaction.entity_type == "lead",
        Interaction.kind == "task",
        Lead.deleted_at.is_(None),
    ]
    if not lead_access.is_head(current_user):
        filters.append(Interaction.assigned_to == current_user.id)
    elif assigned_to is not None:
        filters.append(Interaction.assigned_to == assigned_to)
    term = (q or "").strip()
    if term:
        like = f"%{term.lower()}%"
        full_name = func.lower(func.coalesce(Lead.first_name, "") + " " + func.coalesce(Lead.last_name, ""))
        filters.append(or_(
            full_name.like(like),
            func.lower(func.coalesce(Lead.phone, "")).like(like),
            func.lower(func.coalesce(Interaction.title, "")).like(like),
        ))
    return filters


def _completed_at_subquery():
    """Latest completion time per task, from the activity logs linked through parent_task_id."""
    log = Interaction.__table__.alias("completion_log")
    return (
        select(log.c.parent_task_id.label("task_id"), func.max(func.coalesce(log.c.logged_at, log.c.created_at)).label("completed_at"))
        .where(log.c.kind == "log", log.c.parent_task_id.is_not(None))
        .group_by(log.c.parent_task_id)
        .subquery()
    )


@router.get("/lead-tasks", summary="Follow-ups across all leads, grouped for the Tasks page")
def list_lead_tasks(
    bucket: Bucket = Query("today"),
    assigned_to: int | None = Query(None, description="Admin/manager only; ignored for staff"),
    q: str | None = Query(None, max_length=100, description="Search lead name, phone or task title"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    filters = _base_filters(current_user, assigned_to, q)
    conditions = _conditions(db)

    def _from():
        return select(Interaction).join(Lead, Lead.id == Interaction.entity_id).where(*filters)

    summary = {}
    for name in BUCKETS:
        count_q = select(func.count()).select_from(Interaction).join(Lead, Lead.id == Interaction.entity_id).where(*filters, conditions[name])
        summary[name] = db.scalar(count_q) or 0

    query = _from().where(conditions[bucket])
    if bucket == "completed":
        done = _completed_at_subquery()
        query = query.outerjoin(done, done.c.task_id == Interaction.id).order_by(
            func.coalesce(done.c.completed_at, Interaction.created_at).desc(), Interaction.id.desc()
        )
    else:
        # Oldest due first; tasks without a due time go last.
        query = query.order_by(Interaction.due_at.is_(None), Interaction.due_at.asc(), Interaction.id.asc())
    rows = list(db.scalars(query.offset(skip).limit(limit)).all())

    items = _enrich_many(db, rows)
    leads = {}
    lead_ids = {r.entity_id for r in rows}
    if lead_ids:
        leads = {l.id: l for l in db.scalars(select(Lead).where(Lead.id.in_(lead_ids))).all()}
    for item in items:
        lead = leads.get(item["entity_id"])
        name = " ".join(p for p in ((lead.first_name, lead.last_name) if lead else ()) if p)
        item["lead_name"] = name or f"Lead #{item['entity_id']}"
        item["lead_phone"] = lead.phone if lead else None
        item["lead_status"] = lead.status if lead else None

    return {"items": items, "total": summary[bucket], "skip": skip, "limit": limit, "bucket": bucket, "summary": summary}
