import datetime
import re
from typing import Annotated
from pydantic import BaseModel, EmailStr, ConfigDict, Field, field_validator
from config import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH
from .enums import RoleEnum

_PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")

# A role slug, e.g. "tele_caller". Whether it exists is checked against the `roles` table.
RoleSlug = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")]


def _validate_phone(v: str | None) -> str | None:
    if v is None:
        return v
    cleaned = re.sub(r"[\s\-()]", "", v)
    if not _PHONE_RE.match(cleaned):
        raise ValueError("Phone must be 7-15 digits, optionally starting with +")
    return cleaned


def _validate_password(v: str) -> str:
    if len(v) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters")
    if len(v) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"Password must be at most {PASSWORD_MAX_LENGTH} characters")
    if len(v.encode()) > 72:
        raise ValueError("Password must be at most 72 bytes")
    if not (any(c.isalpha() for c in v) and any(c.isdigit() for c in v)):
        raise ValueError("Password must contain at least one letter and one digit")
    return v


class EmployeeBase(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    email: EmailStr
    role: RoleSlug = RoleEnum.sales.value
    phone: Annotated[str, Field(max_length=20)] | None = None

    @field_validator("name", mode="after")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name must not be blank")
        return v

    @field_validator("email", mode="after")
    @classmethod
    def lowercase_email(cls, v: str) -> str:
        return v.lower()

    @field_validator("phone", mode="after")
    @classmethod
    def clean_phone(cls, v: str | None) -> str | None:
        return _validate_phone(v)


class EmployeeCreate(EmployeeBase):
    password: str

    @field_validator("password", mode="after")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return _validate_password(v)


class EmployeeUpdate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    email: EmailStr | None = None
    role: RoleSlug | None = None
    phone: Annotated[str, Field(max_length=20)] | None = None
    is_active: bool | None = None
    password: str | None = None  # if provided, route handler re-hashes it

    @field_validator("name", mode="after")
    @classmethod
    def strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Name must not be blank")
        return v

    @field_validator("email", mode="after")
    @classmethod
    def lowercase_email(cls, v: str | None) -> str | None:
        return v.lower() if v else v

    @field_validator("phone", mode="after")
    @classmethod
    def clean_phone(cls, v: str | None) -> str | None:
        return _validate_phone(v)

    @field_validator("password", mode="after")
    @classmethod
    def password_strength(cls, v: str | None) -> str | None:
        return None if v is None else _validate_password(v)


class EmployeeSummary(BaseModel):
    """Shallow snapshot used as a nested object inside ContactResponse."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    role: str
    phone: str | None = None


class EmployeeResponse(EmployeeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime
    deleted_at: datetime.datetime | None = None
    # password_hash intentionally excluded


class EmployeeListResponse(BaseModel):
    items: list[EmployeeResponse]
    total: int
    skip: int
    limit: int


class CredentialsEmailStatus(BaseModel):
    sent: bool
    message: str


class EmployeeCreateResponse(EmployeeResponse):
    credentials_email: CredentialsEmailStatus
