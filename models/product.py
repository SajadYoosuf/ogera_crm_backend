import datetime
from decimal import Decimal
from sqlalchemy import JSON, DateTime, ForeignKey, Numeric, SmallInteger, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class ProductOption(Base):
    """A dropdown value for a product: a brand, a category or a material."""

    __tablename__ = "product_options"
    __table_args__ = (UniqueConstraint("kind", "slug", name="uq_product_options_kind_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # brand | category | material
    name: Mapped[str] = mapped_column(String(80))
    slug: Mapped[str] = mapped_column(String(80))
    is_active: Mapped[int] = mapped_column(SmallInteger, default=1)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Basic information
    name: Mapped[str] = mapped_column(String(200), index=True)
    slug: Mapped[str] = mapped_column(String(220), unique=True)
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("product_options.id"), index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("product_options.id"), index=True)
    material_id: Mapped[int | None] = mapped_column(ForeignKey("product_options.id"))
    tagline: Mapped[str | None] = mapped_column(String(200))
    short_description: Mapped[str | None] = mapped_column(Text)
    is_popular: Mapped[int] = mapped_column(SmallInteger, default=0)
    is_active: Mapped[int] = mapped_column(SmallInteger, default=1)

    # Model & capacity
    model_number: Mapped[str | None] = mapped_column(String(80))
    sku: Mapped[str | None] = mapped_column(String(80), unique=True)
    capacity_litres: Mapped[int | None] = mapped_column()
    capacity_display: Mapped[str | None] = mapped_column(String(40))
    orientation: Mapped[str | None] = mapped_column(String(20))  # vertical | horizontal

    features: Mapped[list[str] | None] = mapped_column(JSON)

    # Pricing
    mrp: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    special_price_active: Mapped[int] = mapped_column(SmallInteger, default=0)
    tax_included: Mapped[int] = mapped_column(SmallInteger, default=1)

    # Technical specifications
    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    length_cm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    width_cm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    tank_diameter_mm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    inlet_in: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    outlet_in: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    drain_in: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    thickness_mm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))

    main_image_url: Mapped[str | None] = mapped_column(String(500))

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    brand: Mapped[ProductOption | None] = relationship(foreign_keys=[brand_id])
    category: Mapped[ProductOption | None] = relationship(foreign_keys=[category_id])
    material: Mapped[ProductOption | None] = relationship(foreign_keys=[material_id])
