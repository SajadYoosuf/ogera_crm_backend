-- =============================================================================
-- migrate_leads_table.sql
-- Adds the `leads` table to an existing tenant schema.
--
-- HOW TO USE:
--   For each existing tenant, run:
--       SET search_path TO <schema_name>;
--       \i scripts/migrate_leads_table.sql
--
--   Or run the Python helper:
--       python scripts/run_migration_on_all_tenants.py migrate_leads_table.sql
--
-- WHY A SEPARATE LEADS TABLE?
--   Leads are pre-contact inbound inquiries (from website forms, WhatsApp, Meta Ads).
--   They stay separate from `contacts` until a staff member qualifies and converts them.
--   This prevents the contacts table from being polluted with unqualified or duplicate
--   entries from automated sources.
-- =============================================================================

-- Only create ENUMs if they do not already exist.
-- PostgreSQL enums are schema-scoped (they live in the current search_path schema).
DO $$ BEGIN
    CREATE TYPE lead_source_enum AS ENUM ('website', 'whatsapp', 'meta_ads', 'manual');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE lead_status_enum AS ENUM ('new', 'contacted', 'qualified', 'converted', 'lost');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS leads (
    id               BIGSERIAL     PRIMARY KEY,

    -- Identity
    first_name       VARCHAR(100)  NOT NULL,
    last_name        VARCHAR(100),
    phone            VARCHAR(20),
    -- Last 10 digits of phone — the dedup key (matches phone_key in contacts table).
    phone_key        VARCHAR(10),
    email            VARCHAR(180),

    -- How and where this lead came from.
    source           lead_source_enum NOT NULL DEFAULT 'manual',

    -- Where the lead is in the pre-qualification funnel.
    status           lead_status_enum NOT NULL DEFAULT 'new',

    -- Freeform notes and searchable tags (e.g. ["hot", "plumber"]).
    notes            TEXT,
    tags             TEXT[],

    -- The original payload from the inbound source (form fields, WA message text, sheet row).
    -- Stored as JSONB so we never lose data even if our schema changes later.
    raw_data         JSONB,

    -- Lead quality score (0–100), computed automatically on create/update.
    score            SMALLINT      NOT NULL DEFAULT 0,

    -- WhatsApp template outbound tracking.
    whatsapp_sent    BOOLEAN       NOT NULL DEFAULT FALSE,
    whatsapp_sent_at TIMESTAMPTZ,

    -- Which employee is responsible for following up with this lead.
    -- No FK constraint: schema-per-tenant setups can't resolve cross-table refs
    -- at DDL time if the search_path isn't active. The service layer enforces this.
    assigned_to      BIGINT,

    -- Soft-delete: NULL = live, non-NULL = deleted.
    deleted_at       TIMESTAMPTZ,

    -- When and to which contact this lead was converted.
    converted_at     TIMESTAMPTZ,
    contact_id       BIGINT,

    -- Audit timestamps.
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- Indexes for the most common filter/lookup patterns.
CREATE INDEX IF NOT EXISTS idx_leads_phone_key ON leads(phone_key) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_leads_status    ON leads(status)    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_leads_source    ON leads(source);
CREATE INDEX IF NOT EXISTS idx_leads_assigned  ON leads(assigned_to);
CREATE INDEX IF NOT EXISTS idx_leads_created   ON leads(created_at DESC);
