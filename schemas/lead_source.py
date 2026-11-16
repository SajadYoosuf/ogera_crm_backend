import re
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, field_validator

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _validate_slug(v: str) -> str:
    if not _SLUG_RE.match(v):
        raise ValueError("Slug must be lowercase alphanumeric characters, underscores, or hyphens, starting with alphanumeric")
    return v


def _clean(v: str) -> str:
    v = " ".join(v.split())
    if not v:
        raise ValueError("must not be blank")
    return v


# --- Legacy schemas (kept for backward compatibility with existing imports) ---

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
    """Lightweight read-only snapshot used inside ContactResponse and the active-only dropdown."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str


class LeadSourceResponse(LeadSourceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


# --- CRUD request / response schemas ---

class LeadSourceCreateRequest(BaseModel):
    """
    Body for POST /lead-sources.
    The slug is auto-generated from the name so the caller only needs to supply a human-readable label.
    """
    name: Annotated[str, Field(min_length=1, max_length=60)]

    @field_validator("name", mode="after")
    @classmethod
    def clean_name(cls, v: str) -> str:
        return _clean(v)


class LeadSourceUpdateRequest(BaseModel):
    """Body for PATCH /lead-sources/{id}. All fields are optional (patch semantics)."""
    name: Annotated[str, Field(min_length=1, max_length=60)] | None = None
    is_active: bool | None = None

    @field_validator("name", mode="after")
    @classmethod
    def clean_name(cls, v: str | None) -> str | None:
        return None if v is None else _clean(v)


class LeadSourceAdminResponse(BaseModel):
    """
    Full admin view of a lead source.
    Includes is_active and usage_count so the settings page can show which options are live
    and prevent deletion of options that contacts already reference.
    """
    id: int
    name: str
    slug: str
    is_active: bool
    locked: bool = False      # True for 'field_visit' — the app branches on it, cannot be turned off
    usage_count: int = 0      # number of contacts currently using this source
