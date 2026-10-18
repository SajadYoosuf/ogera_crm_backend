import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from business_time import ist_now

FollowupStatus = Literal["todo", "inprogress", "completed"]
Priority = Literal["high", "medium", "low"]
Outcome = Literal["done", "not_now", "no_answer"]


class FollowupScheduleCreate(BaseModel):
    title: str | None = None
    # An activity type from Settings (call / visit / whatsapp, or any other type stored under its own name).
    channel: str = Field(min_length=1, max_length=15)
    due_at: datetime.datetime
    assigned_to: int
    priority: Priority = "medium"
    notes: str | None = None

    @model_validator(mode="after")
    def validate_due_at(self) -> "FollowupScheduleCreate":
        if self.due_at.replace(tzinfo=None) < ist_now() - datetime.timedelta(minutes=5):
            raise ValueError("due_at cannot be in the past")
        return self


class FollowupScheduleUpdate(BaseModel):
    """Mark completed (status=completed + outcome) OR set inprogress OR reschedule (due_at)."""

    status: FollowupStatus | None = None
    outcome: Outcome | None = None
    notes: str | None = None
    due_at: datetime.datetime | None = None

    @model_validator(mode="after")
    def validate_update(self) -> "FollowupScheduleUpdate":
        if self.status is None and self.due_at is None:
            raise ValueError("provide status or due_at")
        if self.status is not None and self.due_at is not None:
            raise ValueError("set status or reschedule, not both")
        if self.status == "completed" and not self.outcome:
            raise ValueError("outcome is required when marking a follow-up completed")
        if self.outcome and self.status != "completed":
            raise ValueError("outcome is only valid with status=completed")
        if self.due_at is not None and self.due_at.replace(tzinfo=None) < ist_now() - datetime.timedelta(minutes=5):
            raise ValueError("due_at cannot be in the past")
        return self


class FollowupScheduleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int | None = None
    assigned_to: int
    assigned_to_name: str | None = None
    title: str | None = None
    due_at: datetime.datetime
    status: str
    priority: str = "medium"
    channel: str | None = None
    created_at: datetime.datetime
    # enriched contact fields (populated by route)
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_type: str | None = None
    # computed
    notes: str | None = None
    task_state: str = "todo"
    overdue_days: int = 0
