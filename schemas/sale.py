import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

import contact_import


def _blank_to_none(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _clean_phone(value):
    value = _blank_to_none(value)
    if value is None:
        return None
    phone = contact_import.normalize_phone(value)
    if phone is None:
        raise ValueError("Enter a valid phone number, e.g. 9947270717")
    return phone


class SaleBase(BaseModel):
    purchase_date: datetime.date | None = None
    name: str = Field(min_length=1, max_length=150)
    phone: str | None = None
    alt_phone: str | None = None
    address: str | None = None
    purchased_product: str | None = Field(default=None, max_length=150)
    created_by_employee_id: int | None = None

    _text = field_validator("address", "purchased_product", mode="before")(_blank_to_none)
    _phones = field_validator("phone", "alt_phone", mode="before")(_clean_phone)

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value):
        return value.strip() if isinstance(value, str) else value


class SaleEmployeeRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    branch: str | None = None


class SaleCreate(SaleBase):
    pass


class SaleUpdate(BaseModel):
    """Only the fields sent are changed."""

    purchase_date: datetime.date | None = None
    name: str | None = Field(default=None, min_length=1, max_length=150)
    phone: str | None = None
    alt_phone: str | None = None
    address: str | None = None
    purchased_product: str | None = Field(default=None, max_length=150)
    created_by_employee_id: int | None = None

    _text = field_validator("address", "purchased_product", mode="before")(_blank_to_none)
    _phones = field_validator("phone", "alt_phone", mode="before")(_clean_phone)

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value):
        return value.strip() if isinstance(value, str) else value


class SaleResponse(SaleBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int | None = None
    created_by_employee_id: int | None = None
    created_by: SaleEmployeeRef | None = None
    created_at: datetime.datetime


class SaleListResponse(BaseModel):
    items: list[SaleResponse]
    total: int
    skip: int
    limit: int
