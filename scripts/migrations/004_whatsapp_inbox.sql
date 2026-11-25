-- WhatsApp inbox: per-message read state and inbox indexes.
-- Run once per tenant schema, e.g.:  SET search_path TO ventino;  \i scripts/migrations/004_whatsapp_inbox.sql
-- Do NOT run against the live shared database without a backup.

CREATE TABLE IF NOT EXISTS whatsapp_messages (
    id            BIGSERIAL PRIMARY KEY,
    lead_id       BIGINT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    direction     VARCHAR(4) NOT NULL,
    wamid         VARCHAR(100),
    content_type  VARCHAR(20) NOT NULL,
    body          TEXT,
    template_name VARCHAR(100),
    sent_by       BIGINT REFERENCES employees(id) ON DELETE SET NULL,
    status        VARCHAR(20) DEFAULT 'sent',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE whatsapp_messages
    ADD COLUMN IF NOT EXISTS is_read BOOLEAN NOT NULL DEFAULT TRUE;

UPDATE whatsapp_messages SET is_read = FALSE WHERE direction = 'in' AND is_read = TRUE;

CREATE INDEX IF NOT EXISTS idx_wa_lead_created ON whatsapp_messages (lead_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_wa_unread ON whatsapp_messages (lead_id) WHERE direction = 'in' AND is_read = FALSE;
