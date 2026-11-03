"""
Employee API schemas — Pydantic.

These classes define the shape of data coming IN to the API (requests)
and going OUT of the API (responses). They are separate from the DB model.

  EmployeeBase          shared fields for both create and update
  EmployeeCreate        request body for POST /employees
  EmployeeUpdate        request body for PUT /employees/{id}  (all fields optional)
  EmployeeSummary       small read-only shape used inside other responses
  EmployeeResponse      full response for GET / POST / PUT
  EmployeeListResponse  paginated list of EmployeeResponse
  EmployeeCreateResponse  EmployeeResponse + credentials_email status
"""
import datetime
import re
from typing import Annotated
from pydantic import BaseModel, EmailStr, ConfigDict, Field, field_validator
from config import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH
from .enums import RoleEnum

_PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")

# A role slug, e.g. "sales_executive". The format is validated here;
# whether it actually exists in the roles table is checked in the route.
RoleSlug = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")]

VALID_BRANCHES = {"malappuram", "perinthalmanna", "kottakkal"}


def _validate_phone(v: str | None) -> str | None:
    """Strip spaces/dashes, validate format, and normalize to last 10 digits.

    Storing just the last 10 digits makes every Indian number consistent regardless
    of whether the caller included a +91 country code. This lets us put a DB unique
    index on the phone column without needing a generated/virtual column.
    Returns None unchanged.
    """
    if v is None:
        return v
    cleaned = re.sub(r"[\s\-()]", "", v)
    if not _PHONE_RE.match(cleaned):
        raise ValueError("Phone must be 7-15 digits, optionally starting with +")
    digits = re.sub(r"\D", "", cleaned)
    return digits[-10:]


def _validate_password(v: str | None) -> str | None:
    """Check password length and complexity. Returns None unchanged."""
    if v is None:
        return v
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
    """
    Fields shared by Create and Update.
    Validators here run for both — no need to repeat them.
    """
    name: Annotated[str, Field(min_length=1, max_length=120)]
    email: EmailStr
    role: RoleSlug = RoleEnum.sales.value
    phone: Annotated[str, Field(max_length=20)] | None = None
    branch: Annotated[str, Field(max_length=60)] | None = None

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

    @field_validator("branch", mode="after")
    @classmethod
    def validate_branch(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_BRANCHES:
            raise ValueError(f"Branch must be one of: {', '.join(sorted(VALID_BRANCHES))}")
        return v


class EmployeeCreate(EmployeeBase):
    """Request body for POST /employees. Password is required at creation."""
    password: str

    @field_validator("password", mode="after")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return _validate_password(v)


class EmployeeUpdate(EmployeeBase):
    """
    Request body for PUT /employees/{id}.
    Every field is optional — only the fields you send are changed.
    Validators from EmployeeBase are inherited automatically.
    """
    # Override all base fields as optional so the caller only sends what changes.
    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    email: EmailStr | None = None
    role: RoleSlug | None = None
    phone: Annotated[str, Field(max_length=20)] | None = None
    branch: Annotated[str, Field(max_length=60)] | None = None
    is_active: bool | None = None
    password: str | None = None  # if provided, route handler re-hashes it

    @field_validator("password", mode="after")
    @classmethod
    def password_strength(cls, v: str | None) -> str | None:
        return _validate_password(v)


class EmployeeSummary(BaseModel):
    """
    Small read-only snapshot used inside other responses.
    Example: a ContactResponse shows the assigned employee as an EmployeeSummary.
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    role: str
    phone: str | None = None
    branch: str | None = None


class EmployeeResponse(EmployeeBase):
    """Full employee data returned by GET / POST / PUT. Password is never included."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime
    deleted_at: datetime.datetime | None = None


class EmployeeListResponse(BaseModel):
    """Paginated list of employees."""
    items: list[EmployeeResponse]
    total: int
    skip: int
    limit: int


class EmployeeCreateResponse(EmployeeResponse):
    """Returned after creating an employee. Same as EmployeeResponse plus email delivery status."""
    credentials_email: dict  # { sent: bool, message: str }
