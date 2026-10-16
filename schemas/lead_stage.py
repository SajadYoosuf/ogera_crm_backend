import datetime
from pydantic import BaseModel, ConfigDict, model_validator


class LeadStageCreate(BaseModel):
    stage_id: int   # FK to stage_types.id
    changed_by: int  # employee id
    loss_reason: str | None = None  # required when moving to a 'lost' stage
    loss_notes: str | None = None
    awareness_note: str | None = None


class LeadStageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stage_id: int
    stage_slug: str = ""
    stage_name: str = ""
    contact_type: str = ""
    changed_by: int
    changed_at: datetime.datetime
    loss_reason: str | None = None
    loss_notes: str | None = None
    awareness_note: str | None = None
