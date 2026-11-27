"""
The numbers behind GET /dashboard/summary.

VIEWS (chosen here from the signed-in role, never by the client)
    head        admin, manager: everyone, or one employee with ?assigned_to=
    sales       sales_executive
    telecaller  tele_caller
    staff       every other role
Every view except head is limited to the signed-in employee.

SCOPE for one employee
    leads, contacts  assigned to them
    follow-ups       assigned to them (lead and contact follow-ups together)
    activities       done by them
    conversions      converted by them (record_logs), so credit survives a reassignment
    recent updates   changes on their leads and contacts; for a head looking at one employee, that
                     employee's own actions

DEFINITIONS
    open lead      not converted, lost, junk or not qualified
    not contacted  an open lead with no logged activity (status alone is not proof of contact)
    overdue/today  the same rules as the Tasks page (routes/lead_tasks.py)
    conversion     of the leads created in the period, the share that is now converted

Follow-ups, open and unassigned counts, statuses and contact stages are "now". The period applies to new
and converted leads, sources, the trend, activities, loss reasons and WhatsApp messages sent.
"""
import datetime
import logging
from collections import Counter
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from business_time import IST_OFFSET, ist_day_start_utc, today_ist
from models.contact import Contact
from models.employee import Employee
from models.enum_value import EnumValue
from models.interaction import Interaction
from models.lead import Lead
from models.lead_source import LeadSource
from models.lead_stage import LeadStage
from models.lead_status import LeadStatus
from models.pipeline_stage import PipelineStage
from models.record_log import RecordLog
from models.role import Role
from models.whatsapp_config import WhatsAppConfig
from models.whatsapp_message import WhatsAppMessage
from models.whatsapp_template import WhatsAppTemplate
from routes.contacts import _TIMELINE_LABELS as CONTACT_LOG_LABELS
from routes.lead_interactions import _TIMELINE_LABELS as LEAD_LOG_LABELS, _enrich_many
from routes.lead_tasks import _conditions, _db_time
from services import lead_access

logger = logging.getLogger(__name__)

CLOSED_STATUSES = ("converted", "lost_lead", "junk_lead", "not_qualified", "lost")
CALL_TYPES = ("phone_call", "call")
CONTACT_TYPES = ("consumer", "partner", "dealer")
TERMINAL_STAGES = ("lost", "inactive", "inactive_dealer")
OUTCOMES = ("done", "not_now", "no_answer")
QUIET_DAYS = 7
LIST_LIMIT = 8
QUEUE_LIMIT = 20
MAX_RANGE_DAYS = 366
DAILY_BUCKETS_UP_TO = 62  # longer ranges are charted week by week
MIN_TREND_DAYS = 7        # "today" still charts the last week

VIEW_BY_ROLE = {"sales_executive": "sales", "tele_caller": "telecaller"}


@dataclass(frozen=True)
class Period:
    key: str
    date_from: datetime.date
    date_to: datetime.date

    @property
    def start(self) -> datetime.datetime:
        """Naive UTC instant of IST midnight on the first day."""
        return ist_day_start_utc(self.date_from)

    @property
    def end(self) -> datetime.datetime:
        """Naive UTC instant just after the last day (exclusive)."""
        return ist_day_start_utc(self.date_to + datetime.timedelta(days=1))


def resolve_period(key: str, date_from: datetime.date | None, date_to: datetime.date | None) -> Period:
    today = today_ist()
    if key == "custom":
        if date_from is None or date_to is None:
            raise HTTPException(422, detail="Choose both a start date and an end date")
        if date_from > date_to:
            raise HTTPException(422, detail="The start date must be on or before the end date")
        if (date_to - date_from).days >= MAX_RANGE_DAYS:
            raise HTTPException(422, detail="Choose a range of one year or less")
        return Period(key, date_from, date_to)
    days = {"today": 1, "7d": 7, "30d": 30}[key]
    return Period(key, today - datetime.timedelta(days=days - 1), today)


def view_for(employee: Employee) -> str:
    if lead_access.is_head(employee):
        return "head"
    return VIEW_BY_ROLE.get(getattr(employee, "_role_slug", None), "staff")


# ── Small helpers ─────────────────────────────────────────────────────────────

def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _iso(value: datetime.datetime | None) -> str | None:
    """Every timestamp leaves with an explicit UTC offset; naive database values are already UTC."""
    if value is None:
        return None
    if isinstance(value, str):  # SQLite returns text from max() and coalesce()
        value = datetime.datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc).isoformat()


def _ist_date(value: datetime.datetime | str) -> datetime.date:
    if isinstance(value, str):
        value = datetime.datetime.fromisoformat(value)
    if value.tzinfo is not None:
        value = value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return (value + IST_OFFSET).date()


def _lead_name(first: str | None, last: str | None, lead_id: int) -> str:
    return " ".join(p for p in (first, last) if p) or f"Lead #{lead_id}"


def _humanize(value: str) -> str:
    return value.replace("_", " ").capitalize()


def _enum_labels(db: Session, category: str) -> dict[str, str]:
    rows = db.execute(select(EnumValue.value, EnumValue.label).where(EnumValue.category == category)).all()
    return {r.value: r.label for r in rows}


def _employee_names(db: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = db.execute(select(Employee.id, Employee.name).where(Employee.id.in_(ids))).all()
    return {r.id: r.name for r in rows}


def _live_leads(employee_id: int | None) -> list:
    conditions = [Lead.deleted_at.is_(None)]
    if employee_id is not None:
        conditions.append(Lead.assigned_to == employee_id)
    return conditions


def _live_contacts(employee_id: int | None) -> list:
    conditions = [Contact.deleted_at.is_(None)]
    if employee_id is not None:
        conditions.append(Contact.assigned_to == employee_id)
    return conditions


def _open_lead():
    return or_(Lead.status.is_(None), Lead.status.notin_(CLOSED_STATUSES))


def _on_live_record(model_type, model_id, employee_id: int | None = None):
    """Rows (interactions or logs) whose lead or contact still exists, optionally one employee's records."""
    return or_(
        and_(model_type == "lead", model_id.in_(select(Lead.id).where(*_live_leads(employee_id)))),
        and_(model_type == "contact", model_id.in_(select(Contact.id).where(*_live_contacts(employee_id)))),
    )


def _record_names(db: Session, pairs: set[tuple[str, int]]) -> dict[tuple[str, int], tuple[str, str | None]]:
    """(entity_type, id) -> (display name, phone)."""
    out: dict[tuple[str, int], tuple[str, str | None]] = {}
    lead_ids = {i for t, i in pairs if t == "lead"}
    contact_ids = {i for t, i in pairs if t == "contact"}
    if lead_ids:
        for r in db.execute(select(Lead.id, Lead.first_name, Lead.last_name, Lead.phone).where(Lead.id.in_(lead_ids))):
            out[("lead", r.id)] = (_lead_name(r.first_name, r.last_name, r.id), r.phone)
    if contact_ids:
        for r in db.execute(select(Contact.id, Contact.name, Contact.phone).where(Contact.id.in_(contact_ids))):
            out[("contact", r.id)] = (r.name or f"Contact #{r.id}", r.phone)
    return out


def _activity_time():
    return func.coalesce(Interaction.logged_at, Interaction.created_at)


def _activity_in(db: Session, period: Period) -> list:
    return [
        Interaction.kind == "log",
        _activity_time() >= _db_time(db, period.start),
        _activity_time() < _db_time(db, period.end),
    ]


def _buckets(period: Period) -> tuple[datetime.date, int, list[datetime.date]]:
    """First charted day, days per bucket, and the start date of each bucket."""
    first = min(period.date_from, period.date_to - datetime.timedelta(days=MIN_TREND_DAYS - 1))
    span = (period.date_to - first).days + 1
    size = 1 if span <= DAILY_BUCKETS_UP_TO else 7
    return first, size, [first + datetime.timedelta(days=i) for i in range(0, span, size)]


def _bucket_counts(days: list[datetime.date], first: datetime.date, size: int, total: int) -> list[int]:
    counts = [0] * total
    for day in days:
        index = (day - first).days // size
        if 0 <= index < total:
            counts[index] += 1
    return counts


# ── Follow-ups and the work queue ─────────────────────────────────────────────

def _followups(db: Session, employee_id: int | None, period: Period) -> dict:
    conditions = _conditions(db)
    base = [Interaction.kind == "task", _on_live_record(Interaction.entity_type, Interaction.entity_id)]
    if employee_id is not None:
        base.append(Interaction.assigned_to == employee_id)

    done = [*_activity_in(db, period), Interaction.parent_task_id.is_not(None),
            _on_live_record(Interaction.entity_type, Interaction.entity_id)]
    if employee_id is not None:
        done.append(Interaction.done_by == employee_id)

    due_now = or_(conditions["overdue"], conditions["today"])
    cnt = db.execute(
        select(
            func.count(Interaction.id).filter(conditions["overdue"]).label("overdue"),
            func.count(Interaction.id).filter(conditions["today"]).label("today"),
            func.count(Interaction.id).filter(conditions["upcoming"]).label("upcoming"),
            func.count(Interaction.id).filter(due_now).label("due_total"),
        ).where(*base)
    ).one()
    rows = db.scalars(
        select(Interaction).where(*base, due_now)
        .order_by(Interaction.due_at.asc(), Interaction.id.asc()).limit(QUEUE_LIMIT)
    ).all()
    records = _record_names(db, {(r.entity_type, r.entity_id) for r in rows})
    now = datetime.datetime.now(datetime.timezone.utc)

    queue = []
    # The whole interaction, so the web can open the same "complete" form the Tasks page uses
    for item in _enrich_many(db, list(rows)):
        name, phone = records.get((item["entity_type"], item["entity_id"]), ("Unknown", None))
        due = item["due_at"]
        due = due if due.tzinfo else due.replace(tzinfo=datetime.timezone.utc)
        queue.append({
            **item, "name": name, "phone": phone, "due_at": _iso(due), "overdue": due < now,
            "created_at": _iso(item["created_at"]), "logged_at": _iso(item["logged_at"]),
        })

    return {
        "overdue": cnt.overdue or 0,
        "today": cnt.today or 0,
        "upcoming": cnt.upcoming or 0,
        "completed_in_period": db.scalar(
            select(func.count(func.distinct(Interaction.parent_task_id))).where(*done)
        ) or 0,
        "due_total": cnt.due_total or 0,
        "queue": queue,
    }


# ── Leads ─────────────────────────────────────────────────────────────────────

def _lead_items(db: Session, conditions: list, order, limit: int = LIST_LIMIT) -> list[dict]:
    rows = db.scalars(select(Lead).where(*conditions).order_by(*order).limit(limit)).all()
    people = _employee_names(db, {r.assigned_to for r in rows if r.assigned_to})
    sources = {}
    source_ids = {r.source_id for r in rows if r.source_id}
    if source_ids:
        sources = dict(db.execute(select(LeadSource.id, LeadSource.name).where(LeadSource.id.in_(source_ids))).all())
    return [
        {
            "id": r.id, "name": _lead_name(r.first_name, r.last_name, r.id), "phone": r.phone, "status": r.status,
            "source_name": sources.get(r.source_id), "assigned_to_name": people.get(r.assigned_to),
            "created_at": _iso(r.created_at),
        }
        for r in rows
    ]


def _uncontacted(employee_id: int | None) -> list:
    contacted = select(Interaction.entity_id).where(Interaction.entity_type == "lead", Interaction.kind == "log")
    return [*_live_leads(employee_id), _open_lead(), Lead.id.notin_(contacted)]


def _conversion_logs(employee_id: int, period: Period) -> list:
    return [
        RecordLog.entity_type == "lead", RecordLog.action == "converted", RecordLog.actor_id == employee_id,
        RecordLog.created_at >= period.start, RecordLog.created_at < period.end,
        RecordLog.entity_id.in_(select(Lead.id).where(Lead.deleted_at.is_(None))),
    ]


def _leads(db: Session, employee_id: int | None, period: Period) -> dict:
    live = _live_leads(employee_id)
    created = [*live, Lead.created_at >= period.start, Lead.created_at < period.end]
    is_converted = or_(Lead.status == "converted", Lead.converted_at.is_not(None))

    _contacted = select(Interaction.entity_id).where(
        Interaction.entity_type == "lead", Interaction.kind == "log"
    )
    _lead_selects = [
        func.count(Lead.id).filter(_open_lead()).label("open"),
        func.count(Lead.id).filter(
            and_(Lead.created_at >= period.start, Lead.created_at < period.end)
        ).label("new_in_period"),
        func.count(Lead.id).filter(
            and_(Lead.created_at >= period.start, Lead.created_at < period.end, is_converted)
        ).label("cohort_converted"),
        func.count(Lead.id).filter(
            and_(_open_lead(), Lead.id.notin_(_contacted))
        ).label("uncontacted"),
    ]
    if employee_id is None:
        _lead_selects.append(
            func.count(Lead.id).filter(
                and_(Lead.converted_at >= period.start, Lead.converted_at < period.end)
            ).label("converted_in_period")
        )
    lcnt = db.execute(select(*_lead_selects).where(*live)).one()

    new_in_period = lcnt.new_in_period or 0
    cohort_converted = lcnt.cohort_converted or 0

    if employee_id is None:
        converted_in_period = lcnt.converted_in_period or 0
    else:
        converted_in_period = db.scalar(
            select(func.count(func.distinct(RecordLog.entity_id))).where(*_conversion_logs(employee_id, period))
        ) or 0

    # Status now, in the order Settings gives the statuses; "converted" is not a row there, so it goes last
    names = {r.slug: (r.sort_order, r.name) for r in db.scalars(select(LeadStatus)).all()}
    names.setdefault("converted", (10_000, "Converted"))
    status_counts = Counter()
    for status, total in db.execute(select(Lead.status, func.count(Lead.id)).where(*live).group_by(Lead.status)):
        status_counts[status or "new"] += total
    by_status = [
        {"slug": slug, "name": names.get(slug, (9_000, _humanize(slug)))[1], "count": total}
        for slug, total in sorted(status_counts.items(), key=lambda kv: (names.get(kv[0], (9_000, ""))[0], kv[0]))
    ]

    source_names = dict(db.execute(select(LeadSource.id, LeadSource.name)).all())
    totals = dict(db.execute(select(Lead.source_id, func.count(Lead.id)).where(*created).group_by(Lead.source_id)).all())
    wins = dict(db.execute(
        select(Lead.source_id, func.count(Lead.id)).where(*created, is_converted).group_by(Lead.source_id)
    ).all())
    by_source = sorted(
        (
            {"source_id": source_id, "name": source_names.get(source_id, "Unknown"), "count": total,
             "converted": wins.get(source_id, 0)}
            for source_id, total in totals.items()
        ),
        key=lambda row: (-row["count"], row["name"]),
    )

    first, size, starts = _buckets(period)
    chart_start = ist_day_start_utc(first)
    created_days = [
        _ist_date(v) for v in db.scalars(
            select(Lead.created_at).where(*live, Lead.created_at >= chart_start, Lead.created_at < period.end)
        )
    ]
    if employee_id is None:
        converted_values = db.scalars(
            select(Lead.converted_at).where(*live, Lead.converted_at >= chart_start, Lead.converted_at < period.end)
        )
    else:
        chart_period = Period(period.key, first, period.date_to)
        converted_values = db.scalars(
            select(RecordLog.created_at).where(*_conversion_logs(employee_id, chart_period))
        )
    converted_days = [_ist_date(v) for v in converted_values]
    trend = [
        {"date": day.isoformat(), "created": c, "converted": v}
        for day, c, v in zip(
            starts,
            _bucket_counts(created_days, first, size, len(starts)),
            _bucket_counts(converted_days, first, size, len(starts)),
        )
    ]

    uncontacted = _uncontacted(employee_id)
    return {
        "open": lcnt.open or 0,
        "new_in_period": new_in_period,
        "converted_in_period": converted_in_period,
        "cohort_converted": cohort_converted,
        "conversion_rate": round(cohort_converted * 100 / new_in_period, 1) if new_in_period else None,
        "by_status": by_status,
        "by_source": by_source,
        "trend": trend,
        "trend_days_per_point": size,
        "uncontacted": lcnt.uncontacted or 0,
        "uncontacted_items": _lead_items(db, uncontacted, (Lead.created_at.asc(), Lead.id.asc())),
    }


# ── Activity ──────────────────────────────────────────────────────────────────

def _activity(db: Session, employee_id: int | None, period: Period) -> dict:
    scope = [_on_live_record(Interaction.entity_type, Interaction.entity_id)]
    if employee_id is not None:
        scope.append(Interaction.done_by == employee_id)

    rows = db.execute(
        select(Interaction.activity_type, Interaction.outcome, func.count(Interaction.id))
        .where(*scope, *_activity_in(db, period))
        .group_by(Interaction.activity_type, Interaction.outcome)
    ).all()
    by_type: Counter = Counter()
    by_outcome: Counter = Counter()
    calls = calls_with_outcome = calls_answered = 0
    for activity_type, outcome, total in rows:
        by_type[activity_type or "other"] += total
        by_outcome[outcome if outcome in OUTCOMES else "none"] += total
        if activity_type in CALL_TYPES:
            calls += total
            if outcome in OUTCOMES:
                calls_with_outcome += total
                if outcome != "no_answer":
                    calls_answered += total

    first, size, starts = _buckets(period)
    chart = Period(period.key, first, period.date_to)
    chart_rows = db.execute(
        select(_activity_time(), Interaction.activity_type).where(*scope, *_activity_in(db, chart))
    ).all()
    all_days = [_ist_date(when) for when, _ in chart_rows]
    call_days = [_ist_date(when) for when, kind in chart_rows if kind in CALL_TYPES]

    labels = _enum_labels(db, "activity_type")
    return {
        "total": sum(by_type.values()),
        "calls": calls,
        "calls_with_outcome": calls_with_outcome,
        "calls_answered": calls_answered,
        "connect_rate": round(calls_answered * 100 / calls_with_outcome, 1) if calls_with_outcome else None,
        "by_type": [
            {"type": key, "label": labels.get(key, _humanize(key)), "count": total}
            for key, total in sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "by_outcome": {key: by_outcome.get(key, 0) for key in (*OUTCOMES, "none")},
        "by_day": [
            {"date": day.isoformat(), "total": t, "calls": c}
            for day, t, c in zip(
                starts,
                _bucket_counts(all_days, first, size, len(starts)),
                _bucket_counts(call_days, first, size, len(starts)),
            )
        ],
    }


# ── Contacts and their pipelines ──────────────────────────────────────────────

def _current_stage_rows():
    """The newest stage log of every contact (the rule routes/contacts.py uses)."""
    ranked = select(
        LeadStage.contact_id, LeadStage.stage_id, LeadStage.changed_at, LeadStage.loss_reason,
        func.row_number().over(
            partition_by=LeadStage.contact_id, order_by=(LeadStage.changed_at.desc(), LeadStage.id.desc())
        ).label("position"),
    ).subquery()
    return select(ranked).where(ranked.c.position == 1).subquery()


def _contacts(db: Session, employee_id: int | None, period: Period) -> dict:
    scope = _live_contacts(employee_id)
    current = _current_stage_rows()
    counts = db.execute(
        select(Contact.contact_type, current.c.stage_id, func.count(Contact.id))
        .outerjoin(current, current.c.contact_id == Contact.id)
        .where(*scope)
        .group_by(Contact.contact_type, current.c.stage_id)
    ).all()
    by_stage = {(str(kind), stage_id): total for kind, stage_id, total in counts}

    unassigned = {}
    if employee_id is None:
        unassigned = {
            str(kind): total for kind, total in db.execute(
                select(Contact.contact_type, func.count(Contact.id))
                .where(*scope, Contact.assigned_to.is_(None)).group_by(Contact.contact_type)
            )
        }

    stages = db.scalars(
        select(PipelineStage).where(PipelineStage.is_active == 1).order_by(PipelineStage.order, PipelineStage.id)
    ).all()
    pipelines = []
    for kind in CONTACT_TYPES:
        own = [s for s in stages if s.contact_type == kind]
        total = sum(n for (k, _), n in by_stage.items() if k == kind)
        staged = sum(by_stage.get((kind, s.id), 0) for s in own)
        pipelines.append({
            "type": kind,
            "total": total,
            "unassigned": unassigned.get(kind, 0) if employee_id is None else None,
            # Contacts with no stage, or whose stage has since been switched off
            "no_stage": total - staged,
            "stages": [{"id": s.id, "name": s.name, "count": by_stage.get((kind, s.id), 0)} for s in own],
        })

    cutoff = _utc_now() - datetime.timedelta(days=QUIET_DAYS)
    recently_active = select(Interaction.entity_id).where(
        Interaction.entity_type == "contact", Interaction.kind == "log",
        _activity_time() >= _db_time(db, cutoff),
    )
    needs_action = (
        select(Contact.id, Contact.name, Contact.phone, Contact.contact_type, PipelineStage.name.label("stage"))
        .outerjoin(current, current.c.contact_id == Contact.id)
        .outerjoin(PipelineStage, PipelineStage.id == current.c.stage_id)
        .where(
            *scope,
            or_(PipelineStage.slug.is_(None), PipelineStage.slug.notin_(TERMINAL_STAGES)),
            or_(
                current.c.stage_id.is_(None),
                and_(Contact.created_at < cutoff, Contact.id.notin_(recently_active)),
            ),
        )
    )
    _needs = needs_action.add_columns(func.count().over().label("_total"))
    needs_rows = db.execute(
        _needs.order_by(Contact.created_at.asc(), Contact.id.asc()).limit(LIST_LIMIT)
    ).all()
    needs_total = needs_rows[0]._total if needs_rows else 0

    labels = _enum_labels(db, "loss_reason")
    loss_rows = db.execute(
        select(current.c.loss_reason, func.count(Contact.id))
        .join(current, current.c.contact_id == Contact.id)
        .where(
            *scope, current.c.loss_reason.is_not(None),
            current.c.changed_at >= period.start, current.c.changed_at < period.end,
        )
        .group_by(current.c.loss_reason)
    ).all()

    return {
        "total": sum(p["total"] for p in pipelines),
        "pipelines": pipelines,
        "needs_action": needs_total,
        "needs_action_items": [
            {"id": r.id, "name": r.name or f"Contact #{r.id}", "phone": r.phone, "type": str(r.contact_type),
             "stage": r.stage}
            for r in needs_rows
        ],
        "loss_reasons": [
            {"reason": reason, "label": labels.get(reason, _humanize(reason)), "count": total}
            for reason, total in sorted(loss_rows, key=lambda row: (-row[1], row[0]))
        ],
    }


# ── Attention lists ───────────────────────────────────────────────────────────

def _unassigned(db: Session) -> dict:
    conditions = [Lead.deleted_at.is_(None), Lead.assigned_to.is_(None), _open_lead()]
    return {
        "count": db.scalar(select(func.count(Lead.id)).where(*conditions)) or 0,
        "items": _lead_items(db, conditions, (Lead.created_at.asc(), Lead.id.asc())),
    }


def _qualified_with_telecaller(db: Session, employee_id: int | None) -> dict:
    """Pre-qualified leads nobody has handed to sales yet (only a head can reassign a lead)."""
    callers = select(Employee.id).join(Role, Role.id == Employee.role_id).where(Role.slug == "tele_caller")
    conditions = [*_live_leads(employee_id), Lead.status == "pre_qualified", Lead.assigned_to.in_(callers)]
    return {
        "count": db.scalar(select(func.count(Lead.id)).where(*conditions)) or 0,
        "items": _lead_items(db, conditions, (Lead.updated_at.asc(), Lead.id.asc())),
    }


def _newly_assigned(db: Session, employee_id: int, period: Period) -> list[dict]:
    """Leads now assigned to the employee that reached them during the period, newest first."""
    live = _live_leads(employee_id)
    given: dict[int, datetime.datetime] = {}
    # Assigned when it was created: no "assigned" log exists for that
    for lead_id, created_at in db.execute(
        select(Lead.id, Lead.created_at).where(*live, Lead.created_at >= period.start, Lead.created_at < period.end)
        .order_by(Lead.created_at.desc()).limit(LIST_LIMIT)
    ):
        given[lead_id] = created_at
    for lead_id, assigned_at in db.execute(
        select(RecordLog.entity_id, func.max(RecordLog.created_at))
        .where(
            RecordLog.entity_type == "lead", RecordLog.action == "assigned",
            RecordLog.new_value == str(employee_id),
            RecordLog.created_at >= period.start, RecordLog.created_at < period.end,
            RecordLog.entity_id.in_(select(Lead.id).where(*live)),
        )
        .group_by(RecordLog.entity_id)
        .order_by(func.max(RecordLog.created_at).desc()).limit(LIST_LIMIT)
    ):
        given[lead_id] = datetime.datetime.fromisoformat(assigned_at) if isinstance(assigned_at, str) else assigned_at
    if not given:
        return []
    newest = sorted(given, key=lambda i: (given[i], i), reverse=True)[:LIST_LIMIT]
    items = {item["id"]: item for item in _lead_items(db, [Lead.id.in_(newest)], (Lead.id.asc(),))}
    return [{**items[i], "assigned_at": _iso(given[i])} for i in newest]


# ── Recent updates ────────────────────────────────────────────────────────────

def _recent(db: Session, user: Employee, view: str, employee_id: int | None) -> list[dict]:
    q = select(RecordLog).where(RecordLog.entity_type.in_(("lead", "contact")))
    if view == "head":
        q = q.where(_on_live_record(RecordLog.entity_type, RecordLog.entity_id))
        if employee_id is not None:
            q = q.where(RecordLog.actor_id == employee_id)
    else:
        q = q.where(_on_live_record(RecordLog.entity_type, RecordLog.entity_id, user.id))
    rows = db.scalars(q.order_by(RecordLog.created_at.desc(), RecordLog.id.desc()).limit(LIST_LIMIT)).all()

    records = _record_names(db, {(r.entity_type, r.entity_id) for r in rows})
    assign = {"assigned", "task_reassigned"}
    ids = {r.actor_id for r in rows if r.actor_id}
    ids |= {int(r.new_value) for r in rows if r.action in assign and r.new_value and r.new_value.isdigit()}
    people = _employee_names(db, ids)
    statuses = dict(db.execute(select(LeadStatus.slug, LeadStatus.name)).all())

    def detail(r: RecordLog) -> str | None:
        if r.action in assign:
            if r.new_value and r.new_value.isdigit():
                return f"To {people.get(int(r.new_value), 'an employee')}"
            return "Unassigned"
        if r.action in ("status_changed", "stage_changed"):
            parts = [statuses.get(v, _humanize(v)) for v in (r.old_value, r.new_value) if v]
            return " → ".join(parts) or None
        if r.action in ("activity_logged", "task_created", "task_completed") and r.new_value:
            return _humanize(r.new_value) if r.action == "activity_logged" else r.new_value
        return None

    out = []
    for r in rows:
        labels = LEAD_LOG_LABELS if r.entity_type == "lead" else CONTACT_LOG_LABELS
        out.append({
            "id": r.id, "entity_type": r.entity_type, "entity_id": r.entity_id,
            "name": records.get((r.entity_type, r.entity_id), ("Unknown", None))[0],
            "label": labels.get(r.action, _humanize(r.action)), "detail": detail(r),
            "actor_name": people.get(r.actor_id), "created_at": _iso(r.created_at),
        })
    return out


# ── Team (heads) ──────────────────────────────────────────────────────────────

def _team(db: Session, employee_id: int | None, period: Period) -> list[dict]:
    q = (
        select(Employee.id, Employee.name, Role.slug, Role.name.label("role_name"))
        .join(Role, Role.id == Employee.role_id)
        .where(Employee.is_active == 1, Employee.deleted_at.is_(None))
    )
    if employee_id is None:
        q = q.where(Role.slug.notin_(tuple(lead_access.HEAD_ROLES)))
    else:
        q = q.where(Employee.id == employee_id)
    members = db.execute(q).all()

    def grouped(column, counted, *conditions) -> dict[int, int]:
        return dict(db.execute(select(column, func.count(counted)).where(*conditions).group_by(column)).all())

    live_interaction = _on_live_record(Interaction.entity_type, Interaction.entity_id)
    open_leads = grouped(Lead.assigned_to, Lead.id, Lead.deleted_at.is_(None), _open_lead())
    _team_act = db.execute(
        select(
            Interaction.done_by,
            func.count(Interaction.id).label("activities"),
            func.count(Interaction.id).filter(
                Interaction.activity_type.in_(CALL_TYPES)
            ).label("calls"),
            func.count(func.distinct(Interaction.parent_task_id)).filter(
                Interaction.parent_task_id.is_not(None)
            ).label("completed"),
        ).where(live_interaction, *_activity_in(db, period))
        .group_by(Interaction.done_by)
    ).all()
    activities = {r.done_by: r.activities for r in _team_act}
    calls = {r.done_by: r.calls for r in _team_act}
    completed = {r.done_by: r.completed for r in _team_act}
    overdue = grouped(Interaction.assigned_to, Interaction.id, live_interaction, Interaction.kind == "task",
                      _conditions(db)["overdue"])
    converted = grouped(
        RecordLog.actor_id, func.distinct(RecordLog.entity_id),
        RecordLog.entity_type == "lead", RecordLog.action == "converted",
        RecordLog.created_at >= period.start, RecordLog.created_at < period.end,
        RecordLog.entity_id.in_(select(Lead.id).where(Lead.deleted_at.is_(None))),
    )
    rows = [
        {
            "employee_id": m.id, "name": m.name, "role": m.slug, "role_name": m.role_name,
            "open_leads": open_leads.get(m.id, 0), "activities": activities.get(m.id, 0),
            "calls": calls.get(m.id, 0), "followups_completed": completed.get(m.id, 0),
            "overdue": overdue.get(m.id, 0), "converted": converted.get(m.id, 0),
        }
        for m in members
    ]
    return sorted(rows, key=lambda row: (-row["overdue"], row["name"].lower()))


def _employees(db: Session) -> list[dict]:
    rows = db.execute(
        select(Employee.id, Employee.name, Role.slug)
        .join(Role, Role.id == Employee.role_id)
        .where(Employee.is_active == 1, Employee.deleted_at.is_(None))
        .order_by(func.lower(Employee.name))
    ).all()
    return [{"id": r.id, "name": r.name, "role": r.slug} for r in rows]


# ── WhatsApp ──────────────────────────────────────────────────────────────────

def _whatsapp_messages(db: Session, employee_id: int | None, period: Period) -> dict:
    visible = select(Lead.id).where(*_live_leads(employee_id))
    unread = [
        WhatsAppMessage.direction == "in", WhatsAppMessage.status != "read",
        WhatsAppMessage.lead_id.in_(visible),
    ]
    rows = db.execute(
        select(WhatsAppMessage.lead_id, func.count(WhatsAppMessage.id), func.max(WhatsAppMessage.created_at))
        .where(*unread).group_by(WhatsAppMessage.lead_id)
        .order_by(func.max(WhatsAppMessage.created_at).desc()).limit(LIST_LIMIT)
    ).all()
    records = _record_names(db, {("lead", row[0]) for row in rows})
    sent = [
        WhatsAppMessage.direction == "out",
        WhatsAppMessage.created_at >= _db_time(db, period.start),
        WhatsAppMessage.created_at < _db_time(db, period.end),
        WhatsAppMessage.lead_id.in_(select(Lead.id).where(Lead.deleted_at.is_(None))),
    ]
    if employee_id is not None:
        sent.append(WhatsAppMessage.sent_by == employee_id)
    wa_cnt = db.execute(
        select(
            func.count(func.distinct(WhatsAppMessage.lead_id)).filter(and_(*unread)).label("unread_conversations"),
            func.count(WhatsAppMessage.id).filter(and_(*sent)).label("sent_in_period"),
        ).select_from(WhatsAppMessage)
    ).one()
    return {
        "unread_conversations": wa_cnt.unread_conversations or 0,
        "unread_items": [
            {"lead_id": lead_id, "name": records[("lead", lead_id)][0], "unread": total, "last_at": _iso(last)}
            for lead_id, total, last in rows
        ],
        "sent_in_period": wa_cnt.sent_in_period or 0,
    }


def _whatsapp_setup(db: Session) -> dict:
    status = db.scalar(select(WhatsAppConfig.status).limit(1))
    templates = dict(db.execute(
        select(WhatsAppTemplate.status, func.count(WhatsAppTemplate.id)).group_by(WhatsAppTemplate.status)
    ).all())
    return {"status": status or "not_configured", "templates": templates}


def _optional(db: Session, name: str, build):
    """A section whose tables a tenant may not have yet: on a database error it is left out, not the page."""
    try:
        with db.begin_nested():
            return build()
    except SQLAlchemyError as exc:
        logger.warning("dashboard: %s section unavailable: %s", name, exc)
        return None


# ── The summary ───────────────────────────────────────────────────────────────

def build_summary(db: Session, user: Employee, period: Period, assigned_to: int | None) -> dict:
    view = view_for(user)
    if view == "head":
        employee_id = assigned_to
        if employee_id is not None:
            employee = db.get(Employee, employee_id)
            if employee is None or employee.deleted_at is not None:
                raise HTTPException(404, detail="That employee was not found")
    else:
        employee_id = user.id  # staff cannot widen or change their scope
    everyone = view == "head" and employee_id is None

    summary = {
        "view": view,
        "period": {"key": period.key, "date_from": period.date_from.isoformat(), "date_to": period.date_to.isoformat()},
        "employee_id": employee_id,
        "followups": _followups(db, employee_id, period),
        "leads": _leads(db, employee_id, period),
        "activity": _activity(db, employee_id, period),
        "contacts": _contacts(db, employee_id, period),
        "recent": _recent(db, user, view, employee_id),
        "unassigned_leads": _unassigned(db) if everyone else None,
        "qualified_with_telecaller": _qualified_with_telecaller(db, employee_id) if view == "head" else None,
        "newly_assigned": _newly_assigned(db, user.id, period) if view != "head" else None,
        "team": _team(db, employee_id, period) if view == "head" else None,
        "employees": _employees(db) if view == "head" else None,
    }
    summary["whatsapp"] = _optional(db, "WhatsApp", lambda: _whatsapp_messages(db, employee_id, period))
    is_admin = getattr(user, "_role_slug", None) == "admin"
    summary["whatsapp_setup"] = _optional(db, "WhatsApp setup", lambda: _whatsapp_setup(db)) if is_admin else None
    return summary
