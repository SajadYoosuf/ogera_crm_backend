import datetime
from sqlalchemy import BigInteger, Date, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class Sale(Base):
    """A past sale from the sales register. Deliberately not linked to contacts or the products catalogue."""

    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    purchase_date: Mapped[datetime.date | None] = mapped_column(Date, index=True)
    name: Mapped[str] = mapped_column(String(150))
    phone: Mapped[str | None] = mapped_column(String(20), index=True)
    alt_phone: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(Text)
    # Free text: the product name as typed, e.g. "1000L plastic tank".
    purchased_product: Mapped[str | None] = mapped_column(String(150))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
