from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field


class ConsumerProfileBase(BaseModel):
    # Three-state: None=not assessed, 0=sales-ready, 1=needs education
    # Literal enforces the constraint at schema level (no runtime validator needed)
    awareness_mode: Literal[0, 1] | None = None
    house_stage: str | None = None
    current_tank_type: str | None = None
    current_tank_capacity: Annotated[int, Field(ge=0)] | None = None  # litres
    current_tank_age: Annotated[int, Field(ge=0)] | None = None       # years


class ConsumerProfileCreate(ConsumerProfileBase):
    contact_id: int


class ConsumerProfileUpdate(BaseModel):
    awareness_mode: Literal[0, 1] | None = None
    house_stage: str | None = None
    current_tank_type: str | None = None
    current_tank_capacity: Annotated[int, Field(ge=0)] | None = None
    current_tank_age: Annotated[int, Field(ge=0)] | None = None


class ConsumerProfileResponse(ConsumerProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int
