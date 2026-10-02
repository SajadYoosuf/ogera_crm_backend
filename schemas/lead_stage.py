import datetime
from pydantic import BaseModel, ConfigDict, model_validator
from .enums import StageEnum, LossReasonEnum


class LeadStageBase(BaseModel):
    stage: StageEnum
    loss_reason: LossReasonEnum | None = None
    loss_notes: str | None = None
    awareness_note: str | None = None

    @model_validator(mode="after")
    def validate_stage_fields(self) -> "LeadStageBase":
        if self.stage == StageEnum.lost and self.loss_reason is None:
            raise ValueError("loss_reason is required when stage is 'lost'")
        if self.stage != StageEnum.lost and (
            self.loss_reason is not None or self.loss_notes is not None
        ):
            raise ValueError("loss_reason and loss_notes are only valid when stage is 'lost'")
        if self.stage != StageEnum.aware and self.awareness_note is not None:
            raise ValueError("awareness_note is only valid when stage is 'aware'")
        return self


class LeadStageCreate(LeadStageBase):
    # contact_id is NOT here — it comes from the URL path: POST /contacts/{contact_id}/stages
    changed_by: int  # employee id


class LeadStageUpdate(BaseModel):
    # Stage entries are append-only. Only supplementary fields can be corrected.
    loss_reason: LossReasonEnum | None = None
    loss_notes: str | None = None
    awareness_note: str | None = None


class LeadStageResponse(LeadStageBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    # contact_id omitted — always accessed in context of a contact
    changed_by: int
    changed_at: datetime.datetime
