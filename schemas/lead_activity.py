import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from business_time import today_ist

ActivityType = str  # an active option of Settings -> Activity types, checked by the route
TaskState = Literal["pending", "due_today", "overdue", "done"]


class Coordinates(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class LeadActivityCreate(BaseModel):
    """Log something that happened (no schedule, notes required) or schedule a follow-up (`scheduled_date` + `assigned_to`)."""

    type: ActivityType
    notes: str | None = None
    coordinates: Coordinates | None = None  # field visits only
    scheduled_date: datetime.date | None = None
    scheduled_time: datetime.time | None = None
    assigned_to: int | None = None
    status: Literal["pending", "done"] | None = None

    @property
    def is_task(self) -> bool:
        return self.scheduled_date is not None

    @model_validator(mode="after")
    def validate_activity(self) -> "LeadActivityCreate":
        if self.is_task:
            if self.type == "internal_note":
                raise ValueError("an internal note cannot be scheduled")
            if self.assigned_to is None:
                raise ValueError("assigned_to is required for a scheduled follow-up")
            if self.scheduled_date < today_ist():
                raise ValueError("scheduled_date cannot be in the past")
            if self.status == "done":
                raise ValueError("a scheduled follow-up starts as pending")
            if self.coordinates is not None:
                raise ValueError("coordinates are only for a logged field visit")
        else:
            if self.scheduled_time is not None or self.assigned_to is not None:
                raise ValueError("scheduled_time and assigned_to need a scheduled_date")
            if self.status == "pending":
                raise ValueError("a pending activity needs a scheduled_date")
            if not (self.notes and self.notes.strip()):
                raise ValueError("notes are required to log an activity")
            if self.coordinates is not None and self.type != "field_visit":
                raise ValueError("coordinates are only valid for a field visit")
        return self


class LeadActivityUpdate(BaseModel):
    """Mark a pending task done (`status` + `outcome`), or move it (`scheduled_date`, optional `scheduled_time`)."""

    status: Literal["done"] | None = None
    outcome: str | None = None  # what happened; logged as an activity when the task is marked done
    scheduled_date: datetime.date | None = None
    scheduled_time: datetime.time | None = None

    @model_validator(mode="after")
    def validate_update(self) -> "LeadActivityUpdate":
        if self.status is not None and (self.scheduled_date is not None or self.scheduled_time is not None):
            raise ValueError("mark done or reschedule, not both")
        if self.status is None and self.scheduled_date is None:
            raise ValueError("send status or scheduled_date")
        if self.status == "done" and not (self.outcome and self.outcome.strip()):
            raise ValueError("outcome is required to mark a follow-up done")
        if self.status is None and self.outcome is not None:
            raise ValueError("outcome is only for marking a follow-up done")
        if self.scheduled_date is not None and self.scheduled_date < today_ist():
            raise ValueError("scheduled_date cannot be in the past")
        return self


class LeadActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    notes: str | None
    status: str
    scheduled_date: datetime.date | None
    scheduled_time: datetime.time | None
    assigned_to: int | None
    assigned_to_name: str | None = None
    completed_at: datetime.datetime | None
    completed_by: int | None
    completed_by_name: str | None = None
    coordinates: Coordinates | None = None
    created_by: int
    created_by_name: str | None = None
    created_at: datetime.datetime
    task_state: TaskState = "done"
    overdue_days: int = 0


class TaskResponse(LeadActivityResponse):
    """A follow-up task together with the contact it belongs to."""

    contact_id: int
    contact_name: str
    contact_phone: str
    contact_type: str


class TaskSummary(BaseModel):
    overdue: int = 0
    today: int = 0
    upcoming: int = 0
    done: int = 0


class TaskListResponse(BaseModel):
    items: list[TaskResponse]
    total: int
    summary: TaskSummary
