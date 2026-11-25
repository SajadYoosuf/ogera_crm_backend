"""Read and write the tenant's follow-up settings (table `followup_settings`, one row).

get_settings(db) never fails: when the table has not been created yet (the migration script has not
run on this schema) or holds no row, it returns the defaults below. The lookup runs inside a SAVEPOINT
so a missing table does not abort the caller's transaction on PostgreSQL.
"""
import dataclasses
import datetime
import logging

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import config
from models.followup_settings import FollowupSettings

logger = logging.getLogger(__name__)

REMINDER_MODES = ("due_day_only", "daily_until_done")
TASK_STATUS_VALUES = ("todo", "inprogress", "completed")
STATUS_COLORS = ("gray", "blue", "teal", "green", "amber", "red", "purple")
DEFAULT_STATUS_COLORS = {"todo": "gray", "inprogress": "blue", "completed": "teal"}


@dataclasses.dataclass
class FollowupConfig:
    default_due_time: datetime.time = datetime.time(9, 0)
    morning_time: datetime.time = datetime.time(10, 0)
    afternoon_time: datetime.time = datetime.time(14, 0)
    evening_time: datetime.time = datetime.time(17, 0)
    reminders_enabled: bool = True
    reminder_mode: str = "due_day_only"
    reminder_max_days: int = 7
    due_today_reminder_hour: int = 9
    due_now_reminder_enabled: bool = True
    due_now_window_minutes: int = 10
    overdue_reminder_after_hours: int = 24
    retry_followup_days: int = config.RETRY_FOLLOWUP_DAYS
    auto_followup_hours: int = config.AUTO_FOLLOWUP_HOURS
    allow_staff_reschedule: bool = True
    allow_staff_create_followup: bool = True
    status_colors: dict = dataclasses.field(default_factory=lambda: dict(DEFAULT_STATUS_COLORS))
    updated_by: int | None = None
    updated_at: datetime.datetime | None = None
    # False when the values are the built-in defaults because the table or row is missing.
    stored: bool = False


FIELDS = tuple(f.name for f in dataclasses.fields(FollowupConfig) if f.name not in ("updated_by", "updated_at", "stored"))


def _load_row(db: Session) -> FollowupSettings | None:
    """The settings row, or None when the table or row is missing. Raises nothing."""
    try:
        with db.begin_nested():
            return db.scalar(select(FollowupSettings).order_by(FollowupSettings.id).limit(1))
    except SQLAlchemyError as exc:
        logger.info("followup_settings unavailable, using defaults: %s", exc.__class__.__name__)
        return None


def _from_row(row: FollowupSettings) -> FollowupConfig:
    cfg = FollowupConfig(stored=True, updated_by=row.updated_by, updated_at=row.updated_at)
    for name in FIELDS:
        value = getattr(row, name, None)
        if value is None:
            continue
        if name == "status_colors":
            value = {**DEFAULT_STATUS_COLORS, **{k: v for k, v in dict(value).items() if k in TASK_STATUS_VALUES and v in STATUS_COLORS}}
        setattr(cfg, name, value)
    return cfg


def get_settings(db: Session) -> FollowupConfig:
    row = _load_row(db)
    return _from_row(row) if row is not None else FollowupConfig()


def table_exists(db: Session) -> bool:
    try:
        with db.begin_nested():
            db.execute(select(FollowupSettings.id).limit(1))
        return True
    except SQLAlchemyError:
        return False


def save_settings(db: Session, changes: dict, actor_id: int) -> tuple[FollowupConfig, dict]:
    """Apply `changes` to the row (creating it if needed). Returns (new config, {field: (old, new)}). Caller commits."""
    row = _load_row(db)
    if row is None:
        row = FollowupSettings()
        defaults = FollowupConfig()
        for name in FIELDS:
            setattr(row, name, getattr(defaults, name))
        db.add(row)
    before = _from_row(row) if row.id else FollowupConfig()
    diff = {}
    for name, value in changes.items():
        if name == "status_colors":
            value = {**before.status_colors, **value}
        old = getattr(before, name)
        if old != value:
            diff[name] = (old, value)
        setattr(row, name, value)
    row.updated_by = actor_id
    row.updated_at = datetime.datetime.now(datetime.timezone.utc)
    db.flush()
    return _from_row(row), diff


def to_response(cfg: FollowupConfig) -> dict:
    out = {}
    for name in FIELDS:
        value = getattr(cfg, name)
        out[name] = value.strftime("%H:%M") if isinstance(value, datetime.time) else value
    out["updated_by"] = cfg.updated_by
    out["updated_at"] = cfg.updated_at
    out["stored"] = cfg.stored
    return out
