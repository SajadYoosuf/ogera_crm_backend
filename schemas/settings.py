from typing import Annotated
from pydantic import BaseModel, Field, field_validator


def _clean(v: str) -> str:
    v = " ".join(v.split())
    if not v:
        raise ValueError("must not be blank")
    return v


class EnumValueResponse(BaseModel):
    id: int
    value: str
    label: str
    sort_order: int
    is_active: bool
    locked: bool = False  # the app relies on this option: it can be renamed but not turned off
    usage_count: int | None = None  # admin list only
    group: str | None = None  # lead stages: consumer | partner | dealer


class EnumValueCreate(BaseModel):
    label: Annotated[str, Field(min_length=1, max_length=100)]
    value: Annotated[str, Field(max_length=100)] | None = None  # defaults to a slug of the label
    group: Annotated[str, Field(pattern="^(consumer|partner|dealer)$")] | None = None  # lead stages only: the pipeline

    @field_validator("label", mode="after")
    @classmethod
    def clean_label(cls, v: str) -> str:
        return _clean(v)


class EnumValueUpdate(BaseModel):
    label: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    sort_order: Annotated[int, Field(ge=0)] | None = None
    is_active: bool | None = None

    @field_validator("label", mode="after")
    @classmethod
    def clean_label(cls, v: str | None) -> str | None:
        return None if v is None else _clean(v)


class EnumReorder(BaseModel):
    ordered_ids: Annotated[list[int], Field(min_length=1, max_length=500)]


class CompanyResponse(BaseModel):
    company_name: str
    company_logo_url: str | None = None


class CompanyUpdate(BaseModel):
    company_name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    company_logo_url: Annotated[str, Field(max_length=500)] | None = None  # "" removes the logo

    @field_validator("company_name", mode="after")
    @classmethod
    def clean_name(cls, v: str | None) -> str | None:
        return None if v is None else _clean(v)


class PermissionChange(BaseModel):
    allowed: bool


class PermissionCell(BaseModel):
    level: str  # full | view | own | team | customers | none
    note: str | None = None
    editable: bool = False  # the viewer may switch this cell on or off


class PermissionFeature(BaseModel):
    key: str
    label: str
    description: str
    action: str | None = None  # the role_access action this row switches; None = read-only row
    levels: dict[str, PermissionCell]  # role slug -> access


class PermissionRole(BaseModel):
    slug: str
    name: str
    is_system: bool
    is_active: bool


class PermissionMatrix(BaseModel):
    roles: list[PermissionRole]
    features: list[PermissionFeature]
