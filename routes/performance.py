import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session
from auth import get_current_user
from database import get_db
from models import ConsumerProfile, Contact, Employee, LeadActivity, LeadSource, LeadStage

router = APIRouter(prefix="/performance", tags=["performance"])

# Roles that see the whole team; everyone else is pinned to their own numbers.
TEAM_ROLES = {"admin", "manager"}


@router.get("", summary="Lead counts per employee")
def performance_report(
    start_date: datetime.date = Query(...),
    end_date: datetime.date = Query(...),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Counts the contacts each employee created in a date range, split into sales-ready leads and awareness contacts (`awareness_mode = 1`).
    Admins and managers see the whole team. Everyone else sees only their own numbers.
    """
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date")
    is_team = current_user.role in TEAM_ROLES

    awareness = case((ConsumerProfile.awareness_mode == 1, 1), else_=0)
    query = (
        db.query(
            Contact.created_by,
            func.count(Contact.id),
            func.coalesce(func.sum(awareness), 0),
        )
        .outerjoin(ConsumerProfile, ConsumerProfile.contact_id == Contact.id)
        .filter(
            Contact.deleted_at.is_(None),
            Contact.created_at >= start_date,
            Contact.created_at < end_date + datetime.timedelta(days=1),
        )
        .group_by(Contact.created_by)
    )
    if not is_team:
        query = query.filter(Contact.created_by == current_user.id)

    counts = {creator: (int(total), int(aware)) for creator, total, aware in query.all()}
    names = {e.id: e.name for e in db.query(Employee).filter(Employee.deleted_at.is_(None))}

    staff = []
    for employee_id, (total, aware) in counts.items():
        if employee_id is None or employee_id not in names:
            continue
        staff.append({"id": employee_id, "name": names[employee_id], "leads": total - aware, "awareness": aware})
    if not is_team and not staff:
        staff.append({"id": current_user.id, "name": current_user.name, "leads": 0, "awareness": 0})
    staff.sort(key=lambda row: (-(row["leads"] + row["awareness"]), row["name"].lower()))

    orphan_total, orphan_aware = counts.get(None, (0, 0)) if is_team else (0, 0)
    return {
        "scope": "team" if is_team else "self",
        "totals": {
            "leads": sum(row["leads"] for row in staff),
            "awareness": sum(row["awareness"] for row in staff),
        },
        "staff": staff,
        "unattributed": {"leads": orphan_total - orphan_aware, "awareness": orphan_aware},
    }


# --- Dashboard endpoints: summary, team scorecard, pipeline, lead sources -------------------

# Pipeline order, matching the board. Rows saved before the current pipeline ("referral") count as repeat customers.
PIPELINE = [
    ("new_lead", "New Lead"), ("first_call", "First Call"), ("connected", "Connected"), ("aware", "Aware"),
    ("interested", "Interested"), ("quoted", "Quoted"), ("negotiating", "Negotiating"),
    ("won", "Won"), ("lost", "Lost"), ("repeat_customer", "Repeat Customer"),
]
# Roles that are not on the sales team scorecard.
NON_SELLING_ROLES = {"admin", "manager", "support"}


def _period(start_date: datetime.date | None, end_date: datetime.date | None):
    """(start, end, lo, hi): inclusive dates (default: this month so far) and the matching [lo, hi) datetimes."""
    today = datetime.date.today()
    start = start_date or today.replace(day=1)
    end = end_date or today
    if end < start:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date")
    lo = datetime.datetime.combine(start, datetime.time.min)
    hi = datetime.datetime.combine(end + datetime.timedelta(days=1), datetime.time.min)
    return start, end, lo, hi


def _stage_changes(db: Session, stage: str, lo, hi, group_by_assignee: bool, only_user: int | None = None):
    """Distinct contacts that moved into `stage` in [lo, hi), counted from the stage history."""
    distinct = func.count(func.distinct(LeadStage.contact_id))
    columns = [Contact.assigned_to, distinct] if group_by_assignee else [distinct]
    query = (
        db.query(*columns)
        .join(Contact, Contact.id == LeadStage.contact_id)
        .filter(LeadStage.stage == stage, LeadStage.changed_at >= lo, LeadStage.changed_at < hi, Contact.deleted_at.is_(None))
    )
    if only_user is not None:
        query = query.filter(Contact.assigned_to == only_user)
    if group_by_assignee:
        return {assignee: int(count) for assignee, count in query.group_by(Contact.assigned_to).all()}
    return int(query.scalar() or 0)


def _activity_owner():
    """Who an activity counts for: whoever completed it, else whoever logged it."""
    return func.coalesce(LeadActivity.completed_by, LeadActivity.created_by)


def _activity_when():
    return func.coalesce(LeadActivity.completed_at, LeadActivity.created_at)


def _open_followups(db: Session):
    """Pending scheduled follow-ups on live contacts."""
    return (
        db.query(LeadActivity)
        .join(Contact, Contact.id == LeadActivity.contact_id)
        .filter(LeadActivity.status == "pending", Contact.deleted_at.is_(None))
    )


@router.get("/summary", summary="Headline performance numbers")
def performance_summary(
    start_date: datetime.date | None = Query(None),
    end_date: datetime.date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Headline numbers for a period. Admins and managers see the company; everyone else sees their own."""
    start, end, lo, hi = _period(start_date, end_date)
    own = None if current_user.role in TEAM_ROLES else current_user.id

    contacts = db.query(func.count(Contact.id)).filter(Contact.deleted_at.is_(None), Contact.created_at >= lo, Contact.created_at < hi)
    activities = db.query(func.count(LeadActivity.id)).filter(LeadActivity.status == "done", _activity_when() >= lo, _activity_when() < hi)
    overdue = _open_followups(db).filter(LeadActivity.scheduled_date < datetime.date.today())
    if own is not None:
        contacts = contacts.filter(Contact.assigned_to == own)
        activities = activities.filter(_activity_owner() == own)
        overdue = overdue.filter(LeadActivity.assigned_to == own)

    return {
        "scope": "self" if own is not None else "team",
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "total_new_contacts": int(contacts.scalar() or 0),
        "total_activities": int(activities.scalar() or 0),
        "deals_won": _stage_changes(db, "won", lo, hi, False, own),
        "deals_lost": _stage_changes(db, "lost", lo, hi, False, own),
        "overdue_followups": overdue.count(),
    }


@router.get("/team", summary="Team performance table")
def performance_team(
    start_date: datetime.date | None = Query(None),
    end_date: datetime.date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """One row per active seller, most overdue first. Admins and managers only."""
    if current_user.role not in TEAM_ROLES:
        raise HTTPException(status_code=403, detail="You do not have permission to perform this action")
    _, _, lo, hi = _period(start_date, end_date)
    today = datetime.date.today()

    assigned = dict(db.query(Contact.assigned_to, func.count(Contact.id)).filter(Contact.deleted_at.is_(None)).group_by(Contact.assigned_to).all())
    added = dict(
        db.query(Contact.created_by, func.count(Contact.id))
        .filter(Contact.deleted_at.is_(None), Contact.created_at >= lo, Contact.created_at < hi)
        .group_by(Contact.created_by).all()
    )
    activities = dict(
        db.query(_activity_owner(), func.count(LeadActivity.id))
        .filter(LeadActivity.status == "done", _activity_when() >= lo, _activity_when() < hi)
        .group_by(_activity_owner()).all()
    )
    pending = dict(_open_followups(db).with_entities(LeadActivity.assigned_to, func.count(LeadActivity.id)).group_by(LeadActivity.assigned_to).all())
    overdue = dict(
        _open_followups(db).filter(LeadActivity.scheduled_date < today)
        .with_entities(LeadActivity.assigned_to, func.count(LeadActivity.id)).group_by(LeadActivity.assigned_to).all()
    )
    won = _stage_changes(db, "won", lo, hi, True)

    sellers = (
        db.query(Employee)
        .filter(Employee.deleted_at.is_(None), Employee.is_active == 1, Employee.role.notin_(NON_SELLING_ROLES))
        .all()
    )
    rows = [
        {
            "user_id": e.id,
            "name": e.name,
            "contacts_assigned": int(assigned.get(e.id, 0)),
            "contacts_added_period": int(added.get(e.id, 0)),
            "activities_period": int(activities.get(e.id, 0)),
            "followups_pending": int(pending.get(e.id, 0)),
            "followups_overdue": int(overdue.get(e.id, 0)),
            "deals_won_period": int(won.get(e.id, 0)),
        }
        for e in sellers
    ]
    rows.sort(key=lambda row: (-row["followups_overdue"], row["name"].lower()))
    return rows


def _contacts_with_stage(db: Session, *entities):
    """Contacts left-joined to their newest stage row (LeadStage), so callers can group by its stage."""
    latest = (
        db.query(func.max(LeadStage.id).label("stage_id"), LeadStage.contact_id.label("contact_id"))
        .group_by(LeadStage.contact_id)
        .subquery()
    )
    return (
        db.query(*entities)
        .select_from(Contact)
        .outerjoin(latest, latest.c.contact_id == Contact.id)
        .outerjoin(LeadStage, LeadStage.id == latest.c.stage_id)
        .filter(Contact.contact_type == "consumer", Contact.deleted_at.is_(None))
    )


@router.get("/pipeline", summary="Pipeline snapshot")
def performance_pipeline(
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Consumer contacts at each stage right now. This is a snapshot with no dates. Non-managers see only their own contacts."""
    stage = func.coalesce(LeadStage.stage, "new_lead")
    query = _contacts_with_stage(db, stage, func.count(Contact.id))
    if current_user.role not in TEAM_ROLES:
        query = query.filter(Contact.assigned_to == current_user.id)
    counts: dict[str, int] = {}
    for slug, count in query.group_by(stage).all():
        slug = "repeat_customer" if slug == "referral" else slug
        counts[slug] = counts.get(slug, 0) + int(count)
    return [{"stage": slug, "label": label, "count": counts.get(slug, 0)} for slug, label in PIPELINE]


@router.get("/lead-sources", summary="Lead source performance")
def performance_lead_sources(
    start_date: datetime.date | None = Query(None),
    end_date: datetime.date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Consumer contacts created in the period, per lead source, and how many of them are Won now. Biggest source first."""
    _, _, lo, hi = _period(start_date, end_date)
    name = func.coalesce(LeadSource.name, "Unknown")
    won = case((LeadStage.stage == "won", 1), else_=0)
    query = (
        _contacts_with_stage(db, name, func.count(Contact.id), func.coalesce(func.sum(won), 0))
        .outerjoin(LeadSource, LeadSource.id == Contact.lead_source_id)
        .filter(Contact.created_at >= lo, Contact.created_at < hi)
    )
    if current_user.role not in TEAM_ROLES:
        query = query.filter(Contact.assigned_to == current_user.id)
    rows = [{"source": source, "count": int(total), "converted": int(converted)} for source, total, converted in query.group_by(name).all()]
    rows.sort(key=lambda row: (-row["count"], row["source"].lower()))
    return rows
