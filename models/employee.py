import datetime
from sqlalchemy import String, SmallInteger, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(180), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # Slug of a row in `roles` (admins can create new roles).
    role: Mapped[str] = mapped_column(
        String(40), ForeignKey("roles.slug", onupdate="CASCADE"), default="sales_executive"
    )
    phone: Mapped[str | None] = mapped_column(String(20))
    # One of: malappuram, perinthalmanna, kottakkal. Nullable for legacy rows.
    branch: Mapped[str | None] = mapped_column(String(60))
    # FCM device/browser token registered after login. Updated each time the user logs in
    # on a new device. One token per employee (last-registration-wins) for MVP.
    fcm_token: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[int] = mapped_column(SmallInteger, default=1)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    contacts: Mapped[list["Contact"]] = relationship(
        "Contact", back_populates="assigned_employee", foreign_keys="[Contact.assigned_to]"
    )
    lead_stage_changes: Mapped[list["LeadStage"]] = relationship(
        "LeadStage", back_populates="changed_by_employee"
    )
