from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class DealerProfile(Base):
    """B2B details of a dealer contact. The account name lives on contacts.name."""

    __tablename__ = "dealer_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("contacts.id", ondelete="CASCADE"), unique=True
    )
    owner_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(180))

    contact: Mapped["Contact"] = relationship("Contact", back_populates="dealer_profile")
