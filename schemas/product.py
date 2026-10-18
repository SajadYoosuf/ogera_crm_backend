import datetime
import re
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

OptionKind = Literal["brand", "category", "material"]
Orientation = Literal["vertical", "horizontal"]
Money = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]
Measure = Annotated[Decimal, Field(ge=0, max_digits=8, decimal_places=2)]
Feature = Annotated[str, Field(min_length=1, max_length=200)]


def _check_slug(v: str | None) -> str | None:
    if v is not None and not _SLUG_RE.match(v):
        raise ValueError("Slug must be lowercase letters, numbers, hyphens or underscores, starting with a letter or number")
    return v


def _blank_to_none(v):
    return (v.strip() or None) if isinstance(v, str) else v


def _none_to_list(v):
    # Rows saved before features existed hold NULL; they read as an empty list.
    return [] if v is None else v


class ProductOptionCreate(BaseModel):
    kind: OptionKind
    name: Annotated[str, Field(min_length=1, max_length=80)]


class ProductOptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    name: str
    slug: str


class ProductOptionRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class ProductFields(BaseModel):
    """Fields an admin can edit. Everything except name, slug and the main image is optional."""
    brand_id: int | None = None
    category_id: int | None = None
    material_id: int | None = None
    tagline: Annotated[str, Field(max_length=200)] | None = None
    short_description: str | None = None
    is_popular: bool = False
    is_active: bool = True

    model_number: Annotated[str, Field(max_length=80)] | None = None
    sku: Annotated[str, Field(max_length=80)] | None = None
    capacity_litres: Annotated[int, Field(ge=0)] | None = None
    capacity_display: Annotated[str, Field(max_length=40)] | None = None
    orientation: Orientation | None = None

    features: list[Feature] = []

    mrp: Money | None = None
    sale_price: Money | None = None
    currency: Annotated[str, Field(min_length=3, max_length=3)] = "INR"
    special_price_active: bool = False
    tax_included: bool = True

    height_cm: Measure | None = None
    length_cm: Measure | None = None
    width_cm: Measure | None = None
    tank_diameter_mm: Measure | None = None
    inlet_in: Measure | None = None
    outlet_in: Measure | None = None
    drain_in: Measure | None = None
    thickness_mm: Measure | None = None
    weight_kg: Measure | None = None

    main_image_url: Annotated[str, Field(max_length=500)] | None = None

    _blank = field_validator("sku", "model_number", "main_image_url", mode="before")(_blank_to_none)
    _features = field_validator("features", mode="before")(_none_to_list)

    @model_validator(mode="after")
    def sale_not_above_mrp(self):
        if self.mrp is not None and self.sale_price is not None and self.sale_price > self.mrp:
            raise ValueError("Sale price cannot be higher than MRP")
        return self


class ProductCreate(ProductFields):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    slug: Annotated[str, Field(min_length=1, max_length=220)]

    _slug = field_validator("slug")(_check_slug)


class ProductUpdate(BaseModel):
    """PATCH body: only the fields sent are changed."""
    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    slug: Annotated[str, Field(min_length=1, max_length=220)] | None = None
    brand_id: int | None = None
    category_id: int | None = None
    material_id: int | None = None
    tagline: Annotated[str, Field(max_length=200)] | None = None
    short_description: str | None = None
    is_popular: bool | None = None
    is_active: bool | None = None
    model_number: Annotated[str, Field(max_length=80)] | None = None
    sku: Annotated[str, Field(max_length=80)] | None = None
    capacity_litres: Annotated[int, Field(ge=0)] | None = None
    capacity_display: Annotated[str, Field(max_length=40)] | None = None
    orientation: Orientation | None = None
    features: list[Feature] | None = None
    mrp: Money | None = None
    sale_price: Money | None = None
    currency: Annotated[str, Field(min_length=3, max_length=3)] | None = None
    special_price_active: bool | None = None
    tax_included: bool | None = None
    height_cm: Measure | None = None
    length_cm: Measure | None = None
    width_cm: Measure | None = None
    tank_diameter_mm: Measure | None = None
    inlet_in: Measure | None = None
    outlet_in: Measure | None = None
    drain_in: Measure | None = None
    thickness_mm: Measure | None = None
    weight_kg: Measure | None = None
    main_image_url: Annotated[str, Field(max_length=500)] | None = None

    _slug = field_validator("slug")(_check_slug)
    _blank = field_validator("sku", "model_number", "main_image_url", mode="before")(_blank_to_none)
    _features = field_validator("features", mode="before")(_none_to_list)


class ProductResponse(ProductFields):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    brand: ProductOptionRef | None = None
    category: ProductOptionRef | None = None
    material: ProductOptionRef | None = None
    # Computed from mrp and sale_price so web and mobile show the same numbers.
    discount_percent: Decimal = Decimal("0")
    you_save: Decimal = Decimal("0")
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ProductListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    tagline: str | None = None
    brand: ProductOptionRef | None = None
    category: ProductOptionRef | None = None
    model_number: str | None = None
    sku: str | None = None
    capacity_display: str | None = None
    orientation: str | None = None
    mrp: Decimal | None = None
    sale_price: Decimal | None = None
    special_price_active: bool = False
    is_popular: bool = False
    is_active: bool = True
    main_image_url: str | None = None


class ProductListResponse(BaseModel):
    items: list[ProductListItem]
    total: int
    skip: int
    limit: int
