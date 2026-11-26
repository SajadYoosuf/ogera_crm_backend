-- Migration 001 — Contacts Module MVP
-- Schema: ventino
-- All statements are idempotent (IF NOT EXISTS / ON CONFLICT DO NOTHING).
-- Run as: psql -h 187.127.150.170 -U ogera_crm -d ogera_crm -f 001_contacts_module_mvp.sql

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. pipeline_stages
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ventino.pipeline_stages (
    id           SERIAL       PRIMARY KEY,
    name         VARCHAR(60)  NOT NULL,
    slug         VARCHAR(40)  NOT NULL,
    contact_type VARCHAR(20)  NOT NULL,
    description  VARCHAR(255),
    "order"      SMALLINT     NOT NULL DEFAULT 0,
    is_active    SMALLINT     NOT NULL DEFAULT 1,
    CONSTRAINT uq_pipeline_stage_slug_per_type UNIQUE (slug, contact_type)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. lead_stage_log
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ventino.lead_stage_log (
    id             BIGSERIAL  PRIMARY KEY,
    contact_id     BIGINT     NOT NULL REFERENCES ventino.contacts(id),
    stage_id       INT        NOT NULL REFERENCES ventino.pipeline_stages(id),
    changed_by     BIGINT     NOT NULL REFERENCES ventino.employees(id),
    changed_at     TIMESTAMP  NOT NULL DEFAULT now(),
    loss_reason    VARCHAR(40),
    loss_notes     TEXT,
    awareness_note TEXT
);

CREATE INDEX IF NOT EXISTS idx_lead_stage_log_contact_id ON ventino.lead_stage_log(contact_id);
CREATE INDEX IF NOT EXISTS idx_lead_stage_log_stage_id   ON ventino.lead_stage_log(stage_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. contact_assignments
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ventino.contact_assignments (
    id            BIGSERIAL  PRIMARY KEY,
    contact_id    BIGINT     NOT NULL REFERENCES ventino.contacts(id),
    assigned_from BIGINT     REFERENCES ventino.employees(id),
    assigned_to   BIGINT     REFERENCES ventino.employees(id),
    changed_by    BIGINT     NOT NULL REFERENCES ventino.employees(id),
    changed_at    TIMESTAMP  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_contact_assignments_contact_id ON ventino.contact_assignments(contact_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. dealer_profiles
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ventino.dealer_profiles (
    id                  BIGSERIAL     PRIMARY KEY,
    contact_id          BIGINT        NOT NULL UNIQUE REFERENCES ventino.contacts(id),
    business_name       VARCHAR(120),
    gstin               VARCHAR(20),
    town_city           VARCHAR(80),
    pin_code            VARCHAR(10),
    dealer_type         VARCHAR(60),
    assigned_rep        BIGINT        REFERENCES ventino.employees(id),
    credit_limit        NUMERIC(12,2) NOT NULL DEFAULT 0,
    outstanding_balance NUMERIC(12,2) NOT NULL DEFAULT 0,
    data                JSONB         NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_dealer_profiles_contact_id ON ventino.dealer_profiles(contact_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. contact_field_definitions — add show_in_list
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE ventino.contact_field_definitions
    ADD COLUMN IF NOT EXISTS show_in_list BOOLEAN NOT NULL DEFAULT FALSE;

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. Seed pipeline stages (idempotent)
-- ─────────────────────────────────────────────────────────────────────────────

-- Consumer pipeline
INSERT INTO ventino.pipeline_stages (name, slug, contact_type, "order") VALUES
    ('New Lead',                'new_lead',              'consumer', 1),
    ('First Call',              'first_call',            'consumer', 2),
    ('Connected',               'connected',             'consumer', 3),
    ('Aware',                   'aware',                 'consumer', 4),
    ('Interested',              'interested',            'consumer', 5),
    ('Quotation',               'quotation',             'consumer', 6),
    ('Negotiation',             'negotiation',           'consumer', 7),
    ('Won',                     'won',                   'consumer', 8),
    ('Lost',                    'lost',                  'consumer', 9),
    ('After Sales / Referring', 'after_sales_referring', 'consumer', 10)
ON CONFLICT (slug, contact_type) DO NOTHING;

-- Partner pipeline
INSERT INTO ventino.pipeline_stages (name, slug, contact_type, "order") VALUES
    ('New Lead',            'new_lead',          'partner', 1),
    ('First Call',          'first_call',        'partner', 2),
    ('Connected',           'connected',         'partner', 3),
    ('Demo / Introduction', 'demo_introduction', 'partner', 4),
    ('Aware',               'aware',             'partner', 5),
    ('Interested',          'interested',        'partner', 6),
    ('Active',              'active',            'partner', 7),
    ('Referral',            'referral',          'partner', 8),
    ('Inactive',            'inactive',          'partner', 9)
ON CONFLICT (slug, contact_type) DO NOTHING;

-- Dealer pipeline
INSERT INTO ventino.pipeline_stages (name, slug, contact_type, "order") VALUES
    ('Prospect',             'prospect',              'dealer', 1),
    ('Application Received', 'application_received',  'dealer', 2),
    ('Verification',         'verification',          'dealer', 3),
    ('Agreement Signed',     'agreement_signed',      'dealer', 4),
    ('Active Dealer',        'active_dealer',         'dealer', 5),
    ('Inactive',             'inactive_dealer',       'dealer', 6)
ON CONFLICT (slug, contact_type) DO NOTHING;
