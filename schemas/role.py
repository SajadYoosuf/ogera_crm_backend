import datetime
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, field_validator


def _clean_name(v: str) -> str:
    v = " ".join(v.split())
    if not v:
        raise ValueError("Name must not be blank")
    return v


class RoleCreate(BaseModel):
    name: Annotated[str, Field(min_length=2, max_length=80)]
    description: Annotated[str, Field(max_length=255)] | None = None

    @field_validator("name", mode="after")
    @classmethod
    def clean_name(cls, v: str) -> str:
        return _clean_name(v)


_PERMISSION_LETTERS = set("crud")


class RoleUpdate(BaseModel):
    name: Annotated[str, Field(min_length=2, max_length=80)] | None = None
    description: Annotated[str, Field(max_length=255)] | None = None
    is_active: bool | None = None
    see_all_records: bool | None = None
    permissions: dict[str, str] | None = None

    @field_validator("permissions", mode="after")
    @classmethod
    def permission_letters(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return None
        for module, letters in v.items():
            if not set(letters) <= _PERMISSION_LETTERS:
                raise ValueError(f"Permissions for {module} must use only c, r, u, d")
        return {module: "".join(sorted(set(letters))) for module, letters in v.items()}

    @field_validator("name", mode="after")
    @classmethod
    def clean_name(cls, v: str | None) -> str | None:
        return None if v is None else _clean_name(v)


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    description: str | None = None
    is_system: bool
    is_active: bool
    see_all_records: bool = False
    permissions: dict[str, str] = Field(default_factory=dict)
    employee_count: int = 0
    created_at: datetime.datetime
    updated_at: datetime.datetime


class RoleListResponse(BaseModel):
    items: list[RoleResponse]
    total: int
