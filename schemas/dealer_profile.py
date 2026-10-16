from typing import Annotated
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class DealerProfileBase(BaseModel):
    # The dealer's account name is contact.name; the owner is a person at the dealer
    owner_name: Annotated[str, Field(min_length=1, max_length=120)]
    email: EmailStr | None = None


class DealerProfileUpdate(BaseModel):
    owner_name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    email: EmailStr | None = None


class DealerProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int
    owner_name: str
    email: str | None = None
