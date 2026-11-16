import datetime
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, field_validator


class LeadStageCreate(BaseModel):
    stage_id: int = Field(..., gt=0)
    # `changed_by` is kept for backward compatibility so older clients don't get a 422,
    # but the route ignores it and always uses the authenticated user's id instead.
    # This prevents any employee from attributing a stage change to a different employee.
    changed_by: int | None = None
    loss_reason: str | None = None  # required when moving to a 'lost' stage
    loss_notes: Annotated[str, Field(max_length=2000)] | None = None
    awareness_note: Annotated[str, Field(max_length=2000)] | None = None

    @field_validator("loss_reason", "loss_notes", "awareness_note", mode="before")
    @classmethod
    def _strip_blanks(cls, v):
        """Blank strings are treated as absent — prevents empty-string bypasses on required checks."""
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v


class LeadStageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stage_id: int
    stage_slug: str = ""
    stage_name: str = ""
    contact_type: str = ""
    changed_by: int
    changed_by_name: str | None = None
    changed_at: datetime.datetime
    loss_reason: str | None = None
    loss_notes: str | None = None
    awareness_note: str | None = None
