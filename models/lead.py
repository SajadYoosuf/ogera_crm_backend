"""
Lead model — maps to the existing `leads` table in each tenant schema.

COLUMN NOTES:
  first_name      — lead's first name
  last_name       — lead's last name
  phone / phone_key — E.164 phone and last-10-digits dedup key
  email           — optional
  source_id       — FK → lead_sources.id (the marketing channel: Walk-in, Meta Ads, etc.)
  status          — new / contacted / qualified / converted / lost
  notes           — staff notes
  address         — location / city the lead is from
  created_by      — FK → employees.id (who entered this lead)
  assigned_to     — who is following up
  raw_payload     — original inbound JSON
  score           — 0–100 quality score, computed on create/update
  whatsapp_sent   — True after the welcome template is sent
  tags            — searchable text array
  deleted_at      — soft-delete timestamp
  contact_id      — set when lead is converted to a Contact
  converted_at    — timestamp of conversion
"""

import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, SmallInteger, String, Text, func  # noqa: F401
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Lead(Base):
    __tablename__ = "leads"

    # ── Primary key ───────────────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # ── Identity ─────────────────────────────────────────────────────────────
    first_name: Mapped[str | None] = mapped_column(String(200))
    last_name: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(20))
    phone_key: Mapped[str | None] = mapped_column(String(10))
    email: Mapped[str | None] = mapped_column(String(180))

    # ── Provenance ────────────────────────────────────────────────────────────
    # source_id → FK to lead_sources (marketing channel; existing column)
    source_id: Mapped[int | None] = mapped_column(BigInteger)
    # external_ref → reference id from the originating system (existing column)
    external_ref: Mapped[str | None] = mapped_column(String(200))
    # raw_payload → original inbound JSON (existing column, was raw_data in plan)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)

    # ── Status ────────────────────────────────────────────────────────────────
    # Stored as text to match existing column type; validated by Pydantic schemas.
    status: Mapped[str] = mapped_column(String(30), default="new")

    # ── Content ───────────────────────────────────────────────────────────────
    notes: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list | None] = mapped_column(ARRAY(Text))

    # ── Quality score ─────────────────────────────────────────────────────────
    score: Mapped[int] = mapped_column(SmallInteger, default=0)

    # ── WhatsApp outbound ─────────────────────────────────────────────────────
    whatsapp_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    whatsapp_sent_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    # ── People ────────────────────────────────────────────────────────────────
    # created_by → FK to employees.id: the employee who entered this lead.
    # NULL for leads that came in from outside (webhooks, WhatsApp, Excel import).
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="SET NULL"))
    # assigned_to: new column, who is following up
    assigned_to: Mapped[int | None] = mapped_column(BigInteger)

    # Lets code do lead.creator.name instead of looking up the employee by id.
    # foreign_keys is needed because Lead has more than one link to employees
    # in the future; here it makes the join explicit.
    creator: Mapped["Employee | None"] = relationship("Employee", foreign_keys=[created_by])

    # ── Soft delete ───────────────────────────────────────────────────────────
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    # ── Conversion ────────────────────────────────────────────────────────────
    converted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    contact_id: Mapped[int | None] = mapped_column(BigInteger)

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # ── Indexes ───────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("idx_leads_phone_key", "phone_key"),
        Index("idx_leads_status", "status"),
        Index("idx_leads_assigned", "assigned_to"),
        Index("idx_leads_created", "created_at"),
    )
