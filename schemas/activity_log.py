import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, model_validator

Outcome = Literal["done", "not_now", "no_answer"]


class Coordinates(BaseModel):
    lat: float
    lng: float


class ActivityLogCreate(BaseModel):
    activity_type: str
    outcome: Outcome | None = None
    notes: str | None = None
    coordinates: Coordinates | None = None

    @model_validator(mode="after")
    def validate_activity(self) -> "ActivityLogCreate":
        if self.coordinates is not None and self.activity_type != "field_visit":
            raise ValueError("coordinates are only valid for a field_visit")
        return self


class ActivityLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int
    done_by: int
    done_by_name: str | None = None
    activity_type: str
    outcome: str | None
    notes: str | None
    followup_id: int | None
    coordinates: Coordinates | None = None
    logged_at: datetime.datetime


class HistoryItem(BaseModel):
    """A single item in the combined contact timeline."""

    event_type: Literal["stage", "activity"]
    timestamp: datetime.datetime
    actor_id: int
    actor_name: str | None = None
    # stage-change fields
    stage_slug: str | None = None
    stage_name: str | None = None
    stage_note: str | None = None
    # activity fields
    activity_type: str | None = None
    outcome: str | None = None
    notes: str | None = None
    followup_id: int | None = None
