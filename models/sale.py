import datetime
from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class Sale(Base):
    """A past sale from the buyers register. Deliberately not linked to contacts."""

    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    purchase_date: Mapped[datetime.date | None] = mapped_column(Date, index=True)
    name: Mapped[str] = mapped_column(String(150))
    phone: Mapped[str | None] = mapped_column(String(20), index=True)
    alt_phone: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(Text)
    purchased_product: Mapped[str | None] = mapped_column(String(150))
    # The catalogue product, when picked from it; purchased_product then holds that product's name
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
