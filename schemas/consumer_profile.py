from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field
from .enums import HouseStage, TankType


class ConsumerProfileBase(BaseModel):
    house_stage: HouseStage | None = None
    current_tank_type: TankType | None = None
    current_tank_capacity: Annotated[int, Field(ge=0)] | None = None  # litres
    current_tank_age: Annotated[int, Field(ge=0)] | None = None       # years


class ConsumerProfileCreate(ConsumerProfileBase):
    contact_id: int


class ConsumerProfileUpdate(BaseModel):
    house_stage: HouseStage | None = None
    current_tank_type: TankType | None = None
    current_tank_capacity: Annotated[int, Field(ge=0)] | None = None
    current_tank_age: Annotated[int, Field(ge=0)] | None = None


class ConsumerProfileResponse(BaseModel):
    """Responses read as text: rows written before the fixed lists may still hold old values until migrated."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int
    house_stage: str | None = None
    current_tank_type: str | None = None
    current_tank_capacity: int | None = None
    current_tank_age: int | None = None
