-- Leads MVP: tables that do not exist yet in the tenant schema (run once per tenant).
-- Run with the tenant schema selected, e.g.:  SET search_path TO ventino;
-- Safe to re-run: every statement uses IF NOT EXISTS.
-- `interactions`, `leads`, `lead_sources`, `lead_statuses` and `record_logs` already exist: do not touch.

CREATE TABLE IF NOT EXISTS notifications (
    id           BIGSERIAL PRIMARY KEY,
    recipient_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    type         VARCHAR(40)  NOT NULL,
    title        VARCHAR(200) NOT NULL,
    message      TEXT         NOT NULL,
    entity_type  VARCHAR(20),
    entity_id    BIGINT,
    is_read      BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMP    NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_notifications_recipient_id ON notifications (recipient_id);
CREATE INDEX IF NOT EXISTS ix_notifications_created_at   ON notifications (created_at);

CREATE TABLE IF NOT EXISTS whatsapp_messages (
    id            BIGSERIAL PRIMARY KEY,
    lead_id       BIGINT       NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    direction     VARCHAR(4)   NOT NULL,            -- 'in' | 'out'
    wamid         VARCHAR(100),
    content_type  VARCHAR(20)  NOT NULL,            -- 'text' | 'template'
    body          TEXT,
    template_name VARCHAR(100),
    sent_by       BIGINT REFERENCES employees(id) ON DELETE SET NULL,
    status        VARCHAR(20)  NOT NULL DEFAULT 'sent',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wa_lead ON whatsapp_messages (lead_id);
