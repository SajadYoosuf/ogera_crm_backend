"""Home dashboard: one call that feeds every number and list on the screen.

Scope matches the rest of the app. Admin and manager see everything. Other staff see the contacts they created or
are assigned to (the same rule as Contacts), and the follow-ups assigned to them (the same rule as Tasks).
Days are IST calendar days (see business_time.py).
"""
import datetime
from datetime import timedelta
from typing import Literal, NamedTuple

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from auth import get_current_user
from business_time import ist_day_start_utc, today_ist
from contact_access import visible_to
from database import get_db
from models import Contact, Employee, FollowupSchedule, LeadStage, StageType
from routes.contacts import contact_query
from contact_access import visible_assignee_ids

router = APIRouter(prefix="/dashboard", tags=["dashboard"], dependencies=[Depends(get_current_user)])

RangeKey = Literal["today", "7d", "30d", "month", "all"]
RANGE_LABELS = {"today": "Today", "7d": "Last 7 days", "30d": "Last 30 days", "month": "This month", "all": "All time"}
OPEN_STATUSES = ("todo", "inprogress")
FOLLOWUP_LIMIT = 6
RECENT_LIMIT = 6
NO_STAGE = ("no_stage", "No stage")


class Window(NamedTuple):
    """The days the cards count, and the days they are compared with. None on every side means no limit."""
    start: datetime.date | None
    end: datetime.date | None
    previous_start: datetime.date | None
    previous_end: datetime.date | None


def window_for(key: RangeKey, today: datetime.date) -> Window:
    if key == "all":
        return Window(None, None, None, None)
    if key == "today":
        yesterday = today - timedelta(days=1)
        return Window(today, today, yesterday, yesterday)
    if key in ("7d", "30d"):
        days = 7 if key == "7d" else 30
        start = today - timedelta(days=days - 1)
        return Window(start, today, start - timedelta(days=days), start - timedelta(days=1))
    # This month so far, against the same days of last month: 17 Oct is compared with 1 to 17 Sep.
    start = today.replace(day=1)
    last_month_start = (start - timedelta(days=1)).replace(day=1)
    last_month_end = min(last_month_start + timedelta(days=today.day - 1), start - timedelta(days=1))
    return Window(start, today, last_month_start, last_month_end)


class Kpi(BaseModel):
    value: int
    previous: int | None = None
    change: int | None = None


class FollowupKpi(BaseModel):
    value: int  # open follow-ups due today or earlier
    overdue: int  # the part of value that is already late


class Kpis(BaseModel):
    total_contacts: int
    new_leads: Kpi
    open_followups: FollowupKpi
    won: Kpi


class StageCount(BaseModel):
    slug: str
    name: str
    count: int


class FollowupItem(BaseModel):
    id: int
    title: str | None
    due_at: datetime.datetime
    overdue_days: int
    contact_id: int | None
    contact_name: str | None
    contact_phone: str | None
    contact_type: str | None
    assigned_to_name: str | None


class RecentLead(BaseModel):
    id: int
    name: str | None
    phone: str
    contact_type: str
    stage_slug: str | None
    stage_name: str | None
    owner_name: str | None
    created_at: datetime.datetime


class DashboardSummary(BaseModel):
    range: RangeKey
    range_label: str
    today: datetime.date
    start: datetime.date | None
    end: datetime.date | None
    previous_start: datetime.date | None
    previous_end: datetime.date | None
    kpis: Kpis
    stages: list[StageCount]
    followups: list[FollowupItem]
    followups_total: int
    recent_leads: list[RecentLead]


def _contact_filter(start: datetime.date | None, end: datetime.date | None) -> dict:
    """The same filter keys the contact list uses, so the dashboard and Contacts count alike."""
    f = {}
    if start is not None:
        f["created_from"] = start
    if end is not None:
        f["created_to"] = end
    return f


def _count_contacts(db: Session, user: Employee, f: dict) -> int:
    return contact_query(db, f, lambda cur: (func.count(Contact.id),), user).scalar() or 0


def _count_won(db: Session, user: Employee, start: datetime.date | None, end: datetime.date | None) -> int:
    """Contacts that moved into Won during the days. A contact that reached Won twice counts once."""
    q = (
        db.query(func.count(func.distinct(LeadStage.contact_id)))
        .select_from(LeadStage)
        .join(StageType, StageType.id == LeadStage.stage_id)
        .join(Contact, Contact.id == LeadStage.contact_id)
        .filter(StageType.slug == "won", Contact.deleted_at.is_(None))
    )
    scope = visible_to(user)
    if scope is not None:
        q = q.filter(scope)
    if start is not None:
        q = q.filter(LeadStage.changed_at >= ist_day_start_utc(start))
    if end is not None:
        q = q.filter(LeadStage.changed_at < ist_day_start_utc(end + timedelta(days=1)))
    return q.scalar() or 0


def _stage_names(db: Session, stage_ids) -> dict[int, tuple[str, str]]:
    ids = {i for i in stage_ids if i is not None}
    if not ids:
        return {}
    return {st.id: (st.slug, st.name) for st in db.query(StageType).filter(StageType.id.in_(ids)).all()}


def _stage_breakdown(db: Session, user: Employee, f: dict) -> list[StageCount]:
    """Leads in the period, counted by current stage. The same stage name exists in several pipelines,
    so counts are merged by slug. Contacts with no stage are counted as 'No stage'."""
    stage_column = []

    def entities(current):
        stage_column.append(current.stage_id)  # contact_query builds the alias; keep its column for GROUP BY
        return current.stage_id, func.count(Contact.id)

    rows = contact_query(db, f, entities, user).group_by(stage_column[0]).all()
    names = _stage_names(db, (stage_id for stage_id, _ in rows))
    totals: dict[str, StageCount] = {}
    for stage_id, count in rows:
        slug, name = names.get(stage_id, NO_STAGE) if stage_id is not None else NO_STAGE
        bucket = totals.setdefault(slug, StageCount(slug=slug, name=name, count=0))
        bucket.count += count
    return sorted(totals.values(), key=lambda b: (-b.count, b.name))


def _open_followups(db: Session, user: Employee, today: datetime.date):
    """Open follow-ups due today or earlier. Standalone tasks (no contact) count; deleted contacts do not."""
    q = (
        db.query(FollowupSchedule)
        .outerjoin(Contact, FollowupSchedule.contact_id == Contact.id)
        .filter(
            or_(FollowupSchedule.contact_id.is_(None), Contact.deleted_at.is_(None)),
            FollowupSchedule.status.in_(OPEN_STATUSES),
            func.date(FollowupSchedule.due_at) <= today,
        )
    )
    visible = visible_assignee_ids(db, user)
    if visible is not None:
        q = q.filter(FollowupSchedule.assigned_to.in_(visible))
    return q


def _followup_item(fs: FollowupSchedule, today: datetime.date) -> FollowupItem:
    due = fs.due_at.date()
    contact = fs.contact
    return FollowupItem(
        id=fs.id,
        title=fs.title,
        due_at=fs.due_at,
        overdue_days=max((today - due).days, 0),
        contact_id=fs.contact_id,
        contact_name=contact.name if contact else None,
        contact_phone=contact.phone if contact else None,
        contact_type=contact.contact_type if contact else None,
        assigned_to_name=fs.assignee.name if fs.assignee else None,
    )


def _recent_leads(db: Session, user: Employee) -> list[RecentLead]:
    rows = (
        contact_query(db, {}, lambda cur: (Contact, cur), user)
        .options(joinedload(Contact.assigned_employee))
        .order_by(Contact.created_at.desc(), Contact.id.desc())
        .limit(RECENT_LIMIT)
        .all()
    )
    names = _stage_names(db, (cur.stage_id for _, cur in rows if cur is not None))
    items = []
    for contact, cur in rows:
        slug, name = names[cur.stage_id] if cur is not None and cur.stage_id in names else (None, None)
        items.append(RecentLead(
            id=contact.id,
            name=contact.name,
            phone=contact.phone,
            contact_type=contact.contact_type,
            stage_slug=slug,
            stage_name=name,
            owner_name=contact.assigned_employee.name if contact.assigned_employee else None,
            created_at=contact.created_at,
        ))
    return items


def _kpi(value: int, previous: int | None) -> Kpi:
    return Kpi(value=value, previous=previous, change=None if previous is None else value - previous)


@router.get("/summary", response_model=DashboardSummary, summary="Home dashboard numbers and lists")
def dashboard_summary(
    period: RangeKey = Query("month", alias="range", description="today, 7d, 30d, month (this month so far) or all"),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Everything the dashboard shows, in one call.

    - kpis: total contacts (all time), new leads and won in the period with the change from the previous period,
      and open follow-ups due today or earlier (with how many are overdue).
    - stages: leads created in the period, by current stage.
    - followups: the first open follow-ups due today or earlier, most overdue first.
    - recent_leads: the newest contacts, whatever the period.
    """
    today = today_ist()
    win = window_for(period, today)
    has_previous = win.previous_start is not None

    new_now = _count_contacts(db, current_user, _contact_filter(win.start, win.end))
    new_before = _count_contacts(db, current_user, _contact_filter(win.previous_start, win.previous_end)) if has_previous else None
    won_now = _count_won(db, current_user, win.start, win.end)
    won_before = _count_won(db, current_user, win.previous_start, win.previous_end) if has_previous else None

    open_q = _open_followups(db, current_user, today)
    due_total = open_q.count()
    overdue = open_q.filter(func.date(FollowupSchedule.due_at) < today).count()
    followups = (
        open_q.options(joinedload(FollowupSchedule.contact), joinedload(FollowupSchedule.assignee))
        .order_by(FollowupSchedule.due_at.asc(), FollowupSchedule.id.asc())
        .limit(FOLLOWUP_LIMIT)
        .all()
    )

    return DashboardSummary(
        range=period,
        range_label=RANGE_LABELS[period],
        today=today,
        start=win.start,
        end=win.end,
        previous_start=win.previous_start,
        previous_end=win.previous_end,
        kpis=Kpis(
            total_contacts=_count_contacts(db, current_user, {}),
            new_leads=_kpi(new_now, new_before),
            open_followups=FollowupKpi(value=due_total, overdue=overdue),
            won=_kpi(won_now, won_before),
        ),
        stages=_stage_breakdown(db, current_user, _contact_filter(win.start, win.end)),
        followups=[_followup_item(fs, today) for fs in followups],
        followups_total=due_total,
        recent_leads=_recent_leads(db, current_user),
    )
