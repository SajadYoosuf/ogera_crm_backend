import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, model_validator

ActivityKind = Literal["field_visit", "phone_call", "whatsapp", "quote", "demo", "internal_note"]

# Allowed subtypes per kind; internal notes have none
ACTIVITY_SUBTYPES: dict[str, tuple[str, ...]] = {
    "field_visit": ("consumer_visit", "dealer_meeting", "partner_intro"),
    "phone_call": ("check_interest", "quote_followup", "payment_chase"),
    "whatsapp": ("send_quote", "payment_reminder", "festival_offer"),
    "quote": ("new_quote", "revised_price"),
    "demo": ("showroom_demo", "sample_given"),
    "internal_note": (),
}

TaskState = Literal["pending", "due_today", "overdue", "done"]


class LeadActivityCreate(BaseModel):
    kind: ActivityKind
    subtype: str | None = None
    notes: str | None = None
    # Give a due date to schedule a task; leave it out to log something already done
    due_at: datetime.datetime | None = None
    latitude: float | None = None
    longitude: float | None = None

    @model_validator(mode="after")
    def validate_activity(self) -> "LeadActivityCreate":
        allowed = ACTIVITY_SUBTYPES[self.kind]
        if self.subtype is not None and self.subtype not in allowed:
            raise ValueError(f"subtype must be one of {', '.join(allowed) or 'none'} for {self.kind}")
        if self.kind == "internal_note" and not (self.notes and self.notes.strip()):
            raise ValueError("notes are required for an internal note")
        if self.kind == "internal_note" and self.due_at is not None:
            raise ValueError("an internal note cannot be scheduled")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be sent together")
        if self.latitude is not None and self.kind != "field_visit":
            raise ValueError("latitude and longitude are only valid for a field visit")
        return self


class LeadActivityComplete(BaseModel):
    """Mark a pending task done; `outcome` is appended to its notes."""

    outcome: str | None = None


class LeadActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    subtype: str | None
    notes: str | None
    due_at: datetime.datetime | None
    status: str
    done_at: datetime.datetime | None
    latitude: float | None
    longitude: float | None
    created_by: int
    created_by_name: str | None = None
    created_at: datetime.datetime
    task_state: TaskState = "done"
