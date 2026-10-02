import re
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, field_validator

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _validate_slug(v: str) -> str:
    if not _SLUG_RE.match(v):
        raise ValueError("Slug must be lowercase alphanumeric characters, underscores, or hyphens, starting with alphanumeric")
    return v


class LeadSourceBase(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=60)]
    slug: Annotated[str, Field(min_length=1, max_length=40)]
    is_active: bool = True

    @field_validator("slug", mode="after")
    @classmethod
    def slug_format(cls, v: str) -> str:
        return _validate_slug(v)


class LeadSourceCreate(LeadSourceBase):
    pass


class LeadSourceUpdate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=60)] | None = None
    slug: Annotated[str, Field(min_length=1, max_length=40)] | None = None
    is_active: bool | None = None

    @field_validator("slug", mode="after")
    @classmethod
    def slug_format(cls, v: str | None) -> str | None:
        if v is not None:
            return _validate_slug(v)
        return v


class LeadSourceSummary(BaseModel):
    """Lightweight read-only snapshot used inside ContactResponse."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str


class LeadSourceResponse(LeadSourceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
