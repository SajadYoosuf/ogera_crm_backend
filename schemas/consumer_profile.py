from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, field_validator

# House stage and tank type are option values from Settings > Lists & Options (house_stage, tank_type).
# The route checks them against the active options; the schema only cleans the text.
OptionValue = Annotated[str, Field(min_length=1, max_length=50)]


class ConsumerProfileBase(BaseModel):
    house_stage: OptionValue | None = None
    current_tank_type: OptionValue | None = None
    current_tank_capacity: Annotated[int, Field(ge=0)] | None = None  # litres
    current_tank_age: Annotated[int, Field(ge=0)] | None = None       # years

    @field_validator("house_stage", "current_tank_type", mode="before")
    @classmethod
    def blank_is_none(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v


class ConsumerProfileCreate(ConsumerProfileBase):
    contact_id: int


class ConsumerProfileUpdate(ConsumerProfileBase):
    pass


class ConsumerProfileResponse(BaseModel):
    """Responses read as text: rows written before the option lists may still hold old values until migrated."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int
    house_stage: str | None = None
    current_tank_type: str | None = None
    current_tank_capacity: int | None = None
    current_tank_age: int | None = None
