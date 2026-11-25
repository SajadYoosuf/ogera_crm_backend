"""
WhatsApp message template, stored once per (name, language) in each tenant schema.

`status` holds Meta's own words (APPROVED, REJECTED, PAUSED, ...) plus DRAFT for a template that
has not been sent to Meta yet. A template is a draft until it has a `meta_template_id`.

The table is created by hand: see scripts/whatsapp_templates.sql.
"""
import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database import Base

_JSON = JSON().with_variant(JSONB(), "postgresql")


class WhatsAppTemplate(Base):
    __tablename__ = "whatsapp_templates"
    __table_args__ = (UniqueConstraint("name", "language", name="whatsapp_templates_name_language_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(512))
    language: Mapped[str] = mapped_column(String(15), default="en_US")
    category: Mapped[str] = mapped_column(String(15), default="MARKETING")
    header_type: Mapped[str | None] = mapped_column(String(10))
    header_content: Mapped[str | None] = mapped_column(Text)
    header_media_url: Mapped[str | None] = mapped_column(Text)
    header_handle: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str] = mapped_column(Text)
    footer_text: Mapped[str | None] = mapped_column(String(60))
    buttons: Mapped[list | None] = mapped_column(_JSON)
    sample_values: Mapped[dict | None] = mapped_column(_JSON)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    meta_template_id: Mapped[str | None] = mapped_column(String(50))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    quality_score: Mapped[str | None] = mapped_column(String(10))
    submission_error: Mapped[str | None] = mapped_column(Text)
    last_submitted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="SET NULL"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
