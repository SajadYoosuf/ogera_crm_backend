import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, model_validator

Outcome = Literal["done", "not_now", "no_answer"]


class ActivityLogCreate(BaseModel):
    title: str | None = None
    activity_type: str
    outcome: Outcome | None = None
    notes: str | None = None
    logged_at: datetime.datetime | None = None  # defaults to server time if omitted
    done_by: int | None = None  # admin/manager only: log on behalf of another employee

    @model_validator(mode="after")
    def notes_say_what_happened(self) -> "ActivityLogCreate":
        # Every logged activity says what happened; a blank note tells the history nothing.
        if not (self.notes or "").strip():
            raise ValueError("Write what happened in the notes")
        self.notes = self.notes.strip()
        return self


class ActivityLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int
    done_by: int
    done_by_name: str | None = None
    title: str | None = None
    activity_type: str
    outcome: str | None
    notes: str | None
    followup_id: int | None
    logged_at: datetime.datetime


class HistoryItem(BaseModel):
    """A single item in the combined contact timeline."""

    event_type: Literal["stage", "activity", "followup", "followup_rescheduled", "assignment", "followup_edit"]
    timestamp: datetime.datetime
    # Who did it. Empty for a follow-up, since the schedule does not record its creator.
    actor_id: int | None = None
    actor_name: str | None = None
    # stage-change fields
    stage_slug: str | None = None
    stage_name: str | None = None
    previous_stage_name: str | None = None  # the stage this one moved from; empty for the first stage
    stage_note: str | None = None
    # activity fields
    activity_type: str | None = None
    title: str | None = None
    outcome: str | None = None
    notes: str | None = None
    followup_id: int | None = None
    # follow-up fields
    channel: str | None = None
    due_at: datetime.datetime | None = None
    # For a reschedule: the due date before the move (due_at is the new one).
    previous_due_at: datetime.datetime | None = None
    followup_status: str | None = None
    assigned_to_name: str | None = None
    # assignment fields
    assigned_from_name: str | None = None
    # followup_edit fields
    field_name: str | None = None    # which field changed, e.g. 'channel', 'priority', 'due_at'
    old_value: str | None = None
    new_value: str | None = None


class RecentActivityItem(BaseModel):
    """One recent contact event (a contact created or assigned) for the activity log picker.

    `event_type` is "created" or "assigned". For "assigned", `assigned_to_name`
    is the new owner; `actor_name` is who made the change in both cases.
    """

    event_type: Literal["created", "assigned"]
    timestamp: datetime.datetime
    contact_id: int
    contact_name: str | None = None
    actor_id: int | None = None
    actor_name: str | None = None
    assigned_to_name: str | None = None
