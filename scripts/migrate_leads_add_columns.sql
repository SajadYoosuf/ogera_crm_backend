-- =============================================================================
-- migrate_leads_add_columns.sql
-- Adds the new columns required by the Leads module to the existing leads table.
--
-- HOW TO USE:
--   This extends the existing leads table — it does NOT recreate it.
--   Run once per tenant schema:
--       SET search_path TO <schema_name>;
--       \i scripts/migrate_leads_add_columns.sql
--
-- SAFE TO RE-RUN: every statement uses IF NOT EXISTS or ALTER … IF NOT EXISTS.
-- =============================================================================

-- Dedup key: last 10 digits of phone.
-- Shared with the contacts table so we can detect cross-table duplicates.
ALTER TABLE leads ADD COLUMN IF NOT EXISTS phone_key VARCHAR(10);

-- Email address (not in original table)
ALTER TABLE leads ADD COLUMN IF NOT EXISTS email VARCHAR(180);

-- Last name (original table only had `name`)
ALTER TABLE leads ADD COLUMN IF NOT EXISTS last_name VARCHAR(100);

-- Lead quality score 0–100, computed by the service layer on every create/update
ALTER TABLE leads ADD COLUMN IF NOT EXISTS score SMALLINT NOT NULL DEFAULT 0;

-- WhatsApp auto-reply tracking
ALTER TABLE leads ADD COLUMN IF NOT EXISTS whatsapp_sent    BOOLEAN     NOT NULL DEFAULT FALSE;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS whatsapp_sent_at TIMESTAMPTZ;

-- Who is responsible for following up (different from created_by which is who entered it)
ALTER TABLE leads ADD COLUMN IF NOT EXISTS assigned_to BIGINT;

-- Soft delete: NULL = live, timestamp = deleted
ALTER TABLE leads ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

-- Conversion tracking
ALTER TABLE leads ADD COLUMN IF NOT EXISTS converted_at TIMESTAMPTZ;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS contact_id   BIGINT;

-- Searchable text labels e.g. {"hot", "plumber", "malappuram"}
ALTER TABLE leads ADD COLUMN IF NOT EXISTS tags TEXT[];

-- Audit timestamp for updates
ALTER TABLE leads ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Indexes for the new columns
CREATE INDEX IF NOT EXISTS idx_leads_phone_key  ON leads(phone_key)  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_leads_status     ON leads(status)     WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_leads_assigned   ON leads(assigned_to);
CREATE INDEX IF NOT EXISTS idx_leads_created    ON leads(created_at DESC);
