"""
WhatsApp connection settings.

WhatsAppConfig lives in each tenant schema and holds that tenant's Meta credentials.
The access token and app secret are stored encrypted (see services/crypto.py).

WhatsAppTenantMap lives in the public schema. An inbound webhook has no login, so it
uses the WABA id from Meta's payload to find the tenant schema.

The tables are created by hand: see scripts/whatsapp_config.sql.
"""
import datetime
import os

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base

# SQLite (used by tests) does not support named schemas.
_PUBLIC = {} if os.getenv("DATABASE_URL", "").startswith("sqlite") else {"schema": "public"}


class WhatsAppConfig(Base):
    __tablename__ = "whatsapp_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    phone_number_id: Mapped[str] = mapped_column(String(50))
    waba_id: Mapped[str] = mapped_column(String(50))
    app_id: Mapped[str | None] = mapped_column(String(50))
    business_id: Mapped[str | None] = mapped_column(String(50))
    api_version: Mapped[str] = mapped_column(String(10), default="v21.0")
    access_token_enc: Mapped[str] = mapped_column(Text)
    app_secret_enc: Mapped[str | None] = mapped_column(Text)
    verify_token: Mapped[str] = mapped_column(String(100))
    display_phone_number: Mapped[str | None] = mapped_column(String(30))
    verified_name: Mapped[str | None] = mapped_column(String(100))
    quality_rating: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(15), default="disconnected")
    connected_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="SET NULL"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WhatsAppTenantMap(Base):
    __tablename__ = "whatsapp_tenant_map"
    __table_args__ = _PUBLIC

    waba_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    phone_number_id: Mapped[str | None] = mapped_column(String(50), unique=True)
    schema_name: Mapped[str] = mapped_column(String(63))
