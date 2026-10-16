from sqlalchemy import BigInteger, ForeignKey, SmallInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class ConsumerProfile(Base):
    __tablename__ = "consumer_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("contacts.id", ondelete="CASCADE"), unique=True
    )
    # Fixed list in code (schemas/enums.py HouseStage); VARCHAR so legacy text can still be read until migrated
    house_stage: Mapped[str | None] = mapped_column(String(50))
    current_tank_type: Mapped[str | None] = mapped_column(String(50))
    current_tank_capacity: Mapped[int | None] = mapped_column(Integer)  # litres
    current_tank_age: Mapped[int | None] = mapped_column(Integer)       # years

    contact: Mapped["Contact"] = relationship("Contact", back_populates="consumer_profile")
