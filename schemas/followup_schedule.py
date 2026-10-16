import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, model_validator

Channel = Literal["call", "visit", "whatsapp"]
FollowupStatus = Literal["pending", "done", "skipped"]
Outcome = Literal["done", "not_now", "no_answer"]


class FollowupScheduleCreate(BaseModel):
    channel: Channel
    due_at: datetime.datetime
    assigned_to: int
    notes: str | None = None

    @model_validator(mode="after")
    def validate_due_at(self) -> "FollowupScheduleCreate":
        if self.due_at.replace(tzinfo=None) < datetime.datetime.now() - datetime.timedelta(minutes=5):
            raise ValueError("due_at cannot be in the past")
        return self


class FollowupScheduleUpdate(BaseModel):
    """Mark done (status=done + outcome) OR reschedule (due_at) OR skip (status=skipped)."""

    status: FollowupStatus | None = None
    outcome: Outcome | None = None
    notes: str | None = None
    due_at: datetime.datetime | None = None

    @model_validator(mode="after")
    def validate_update(self) -> "FollowupScheduleUpdate":
        if self.status is None and self.due_at is None:
            raise ValueError("provide status or due_at")
        if self.status is not None and self.due_at is not None:
            raise ValueError("mark done/skip or reschedule, not both")
        if self.status == "done" and not self.outcome:
            raise ValueError("outcome is required when marking a follow-up done")
        if self.status == "skipped" and self.outcome:
            raise ValueError("outcome is only for marking done")
        if self.outcome and self.status != "done":
            raise ValueError("outcome is only valid with status=done")
        if self.due_at is not None and self.due_at.replace(tzinfo=None) < datetime.datetime.now() - datetime.timedelta(minutes=5):
            raise ValueError("due_at cannot be in the past")
        return self


class FollowupScheduleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int
    assigned_to: int
    assigned_to_name: str | None = None
    due_at: datetime.datetime
    status: str
    channel: str
    created_at: datetime.datetime
    # enriched contact fields (populated by route)
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_type: str | None = None
    # computed
    notes: str | None = None
    task_state: str = "pending"
    overdue_days: int = 0
