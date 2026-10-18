from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .enums import profession_key

# Checked against the Partner types list (settings_catalog, PROFESSIONAL_TYPE) in the route, not here.
Profession = Annotated[str, Field(min_length=1, max_length=80)]


class PartnerProfileBase(BaseModel):
    profession_type: Profession

    @field_validator("profession_type", mode="before")
    @classmethod
    def _key(cls, value):
        return profession_key(value) if isinstance(value, str) else value
    work_area: Annotated[str, Field(max_length=120)] | None = None
    sample_given: bool | None = None
    projects_per_year: Annotated[int, Field(ge=0)] | None = None
    # referrals_sent is intentionally absent — derived at query time


class PartnerProfileCreate(PartnerProfileBase):
    contact_id: int


class PartnerProfileUpdate(BaseModel):
    profession_type: Profession | None = None

    @field_validator("profession_type", mode="before")
    @classmethod
    def _key(cls, value):
        return profession_key(value) if isinstance(value, str) else value
    work_area: Annotated[str, Field(max_length=120)] | None = None
    sample_given: bool | None = None
    projects_per_year: Annotated[int, Field(ge=0)] | None = None


class PartnerProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int
    profession_type: str
    work_area: str | None = None
    sample_given: bool | None = None
    projects_per_year: int | None = None

    @field_validator("profession_type", mode="before")
    @classmethod
    def _normalise_legacy(cls, value):
        # Rows written before the fixed list hold free text such as "Plumber" or "Interior Designer"
        if isinstance(value, str):
            return profession_key(value)
        return value
