"""
Follow-up settings — timings, reminder rules and staff permissions for lead follow-ups.

ENDPOINTS:
    GET   /settings/followups   — any signed-in user (the web app needs the preset times and flags)
    PATCH /settings/followups   — admin or manager (roles with see_all_records); writes a record_logs entry

Values live in the tenant's `followup_settings` table (scripts/migrate_followup_settings.sql).
Before that script runs, GET returns the defaults and PATCH answers 503.
"""
import datetime
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from auth import get_current_user, get_tenant_db
from models.employee import Employee
from services import followup_settings as fs
from services import lead_access
from services.record_log_service import add_log

router = APIRouter(prefix="/settings", tags=["settings"])


def _parse_time(value: str | datetime.time) -> datetime.time:
    if isinstance(value, datetime.time):
        return value.replace(second=0, microsecond=0)
    try:
        hours, minutes = str(value).strip().split(":")[:2]
        return datetime.time(int(hours), int(minutes))
    except (ValueError, TypeError):
        raise ValueError("Use a time like 09:30") from None


class FollowupSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_due_time: datetime.time | None = None
    morning_time: datetime.time | None = None
    afternoon_time: datetime.time | None = None
    evening_time: datetime.time | None = None
    reminders_enabled: bool | None = None
    reminder_mode: Literal["due_day_only", "daily_until_done"] | None = None
    reminder_max_days: int | None = Field(default=None, ge=0, le=90)
    due_today_reminder_hour: int | None = Field(default=None, ge=0, le=23)
    due_now_reminder_enabled: bool | None = None
    due_now_window_minutes: int | None = Field(default=None, ge=5, le=120)
    overdue_reminder_after_hours: int | None = Field(default=None, ge=1, le=720)
    retry_followup_days: int | None = Field(default=None, ge=1, le=60)
    auto_followup_hours: int | None = Field(default=None, ge=1, le=720)
    allow_staff_reschedule: bool | None = None
    allow_staff_create_followup: bool | None = None
    status_colors: dict[str, str] | None = None

    @field_validator("default_due_time", "morning_time", "afternoon_time", "evening_time", mode="before")
    @classmethod
    def _time(cls, v):
        return None if v is None else _parse_time(v)

    @field_validator("status_colors")
    @classmethod
    def _colors(cls, v):
        if v is None:
            return v
        for status, color in v.items():
            if status not in fs.TASK_STATUS_VALUES:
                raise ValueError(f"Unknown task status '{status}'")
            if color not in fs.STATUS_COLORS:
                raise ValueError(f"Colour must be one of: {', '.join(fs.STATUS_COLORS)}")
        return v


@router.get("/followups", summary="Follow-up timings, reminder rules and staff permissions")
def get_followup_settings(db: Session = Depends(get_tenant_db), _: Employee = Depends(get_current_user)):
    return fs.to_response(fs.get_settings(db))


def _jsonable(value):
    return value.strftime("%H:%M") if isinstance(value, datetime.time) else value


@router.patch("/followups", summary="Change follow-up settings (admin or manager)")
def update_followup_settings(
    body: FollowupSettingsUpdate,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    lead_access.ensure_is_head(current_user, "change follow-up settings")
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not changes:
        raise HTTPException(status_code=422, detail="Nothing to change")
    if not fs.table_exists(db):
        raise HTTPException(
            status_code=503,
            detail="Follow-up settings are not set up on this database yet. Ask your administrator to run the setup script.",
        )
    cfg, diff = fs.save_settings(db, changes, current_user.id)
    if diff:
        add_log(
            db, "settings", 0, "followup_settings_updated", current_user.id,
            old_value=json.dumps({k: _jsonable(old) for k, (old, _new) in diff.items()}),
            new_value=json.dumps({k: _jsonable(new) for k, (_old, new) in diff.items()}),
        )
    db.commit()
    return fs.to_response(cfg)
