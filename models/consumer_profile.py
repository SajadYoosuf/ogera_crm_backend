from sqlalchemy import BigInteger, ForeignKey, SmallInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class ConsumerProfile(Base):
    __tablename__ = "consumer_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("contacts.id", ondelete="CASCADE"), unique=True
    )
    # None = not assessed, 1 = needs education (aware stage), 0 = sales-ready
    awareness_mode: Mapped[int | None] = mapped_column(SmallInteger)
    # VARCHAR so values like "Under Construction - Structure" / "Pvc" are stored as-is
    house_stage: Mapped[str | None] = mapped_column(String(50))
    current_tank_type: Mapped[str | None] = mapped_column(String(50))
    current_tank_capacity: Mapped[int | None] = mapped_column(Integer)  # litres
    current_tank_age: Mapped[int | None] = mapped_column(Integer)       # years

    contact: Mapped["Contact"] = relationship("Contact", back_populates="consumer_profile")
