"""
WhatsAppMessage model — stores sent and received messages for a lead's inbox tab.

The table must be created by the DBA before this code is deployed:

    CREATE TABLE whatsapp_messages (
        id            BIGSERIAL PRIMARY KEY,
        lead_id       BIGINT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
        direction     VARCHAR(4) NOT NULL,       -- 'in' | 'out'
        wamid         VARCHAR(100),              -- Meta's message id (for delivery receipts)
        content_type  VARCHAR(20) NOT NULL,      -- 'text' | 'template' | 'image' | 'document'
        body          TEXT,                      -- text content
        template_name VARCHAR(100),              -- if content_type='template'
        sent_by       BIGINT REFERENCES employees(id) ON DELETE SET NULL,
        status        VARCHAR(20) DEFAULT 'sent', -- sent / delivered / read / failed
        created_at    TIMESTAMPTZ DEFAULT NOW()
    );
"""
import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class WhatsAppMessage(Base):
    __tablename__ = "whatsapp_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("leads.id", ondelete="CASCADE"))
    direction: Mapped[str] = mapped_column(String(4))          # 'in' | 'out'
    wamid: Mapped[str | None] = mapped_column(String(100))     # Meta message id
    content_type: Mapped[str] = mapped_column(String(20))      # 'text' | 'template'
    body: Mapped[str | None] = mapped_column(Text)
    template_name: Mapped[str | None] = mapped_column(String(100))
    sent_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="sent")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_wa_lead", "lead_id"),
    )
