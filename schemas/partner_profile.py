from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .enums import ProfessionType


class PartnerProfileBase(BaseModel):
    # Three-state: None=not assessed, 0=sales-ready, 1=needs education
    awareness_mode: Literal[0, 1] | None = None
    profession_type: ProfessionType
    work_area: Annotated[str, Field(max_length=120)] | None = None
    sample_given: bool | None = None
    projects_per_year: Annotated[int, Field(ge=0)] | None = None
    # referrals_sent is intentionally absent — derived at query time


class PartnerProfileCreate(PartnerProfileBase):
    contact_id: int


class PartnerProfileUpdate(BaseModel):
    awareness_mode: Literal[0, 1] | None = None
    profession_type: ProfessionType | None = None
    work_area: Annotated[str, Field(max_length=120)] | None = None
    sample_given: bool | None = None
    projects_per_year: Annotated[int, Field(ge=0)] | None = None


class PartnerProfileResponse(PartnerProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int

    @field_validator("profession_type", mode="before")
    @classmethod
    def _normalise_legacy(cls, value):
        # Rows written before the enum hold free text such as "Plumber" or "Interior Designer"
        if isinstance(value, str):
            return ProfessionType.normalise(value)
        return value
