-- PostgreSQL migration: contacts module for ventino tenant schema
-- Run against the PostgreSQL ogera_crm database with:
--   psql -h 187.127.150.170 -U ogera_crm -d ogera_crm -f scripts/migrate_contacts_pg.sql
--
-- What this creates in the ventino schema:
--   branches               — tenant-local branch definitions (seed: malappuram, kottakkal, perinthalmanna)
--   contact_field_defs     — admin-editable field definitions + dropdown options per entity_type
--   contacts               — base contact data with branch_id FK and lead_id FK
--   consumer_profiles      — JSONB data bag for consumer-specific answers
--   partner_profiles       — JSONB data bag for partner-specific answers
--
-- Why JSONB for profile data:
--   Each tenant defines their own profile fields at onboarding.
--   Ventino uses tank_type/house_stage; another tenant might use panel_type/voltage.
--   Storing answers in JSONB means zero SQL or Python code change per new tenant.
--   The field definitions live in contact_field_defs and are admin-editable.

BEGIN;
SET search_path TO ventino;

-- ─── 1. branches ──────────────────────────────────────────────────────────────
CREATE TABLE branches (
    id         BIGSERIAL    PRIMARY KEY,
    name       VARCHAR(100) NOT NULL,
    code       VARCHAR(30)  NOT NULL UNIQUE,
    is_active  BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

INSERT INTO branches (name, code) VALUES
    ('Malappuram',     'malappuram'),
    ('Kottakkal',      'kottakkal'),
    ('Perinthalmanna', 'perinthalmanna');

-- ─── 2. contact_field_definitions ─────────────────────────────────────────────
-- Defines which extra questions exist for consumers and partners, and what their
-- dropdown options are. Admin can edit these after onboarding without any code change.
CREATE TABLE contact_field_definitions (
    id           BIGSERIAL    PRIMARY KEY,
    entity_type  VARCHAR(10)  NOT NULL CHECK (entity_type IN ('consumer', 'partner')),
    field_name   VARCHAR(50)  NOT NULL,
    field_label  VARCHAR(100),
    field_type   VARCHAR(20)  NOT NULL CHECK (field_type IN ('text', 'dropdown', 'number', 'boolean')),
    options      JSONB,        -- non-null only for field_type='dropdown'; list of allowed values
    is_required  BOOLEAN      NOT NULL DEFAULT FALSE,
    sort_order   INT          NOT NULL DEFAULT 0,
    is_active    BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (entity_type, field_name)
);

-- Seed: initial field definitions for ventino (admin can edit/add after onboarding)
INSERT INTO contact_field_definitions
    (entity_type, field_name, field_label, field_type, options, is_required, sort_order) VALUES
    ('consumer', 'house_stage',  'House Stage',       'dropdown',
     '["under_construction","ready_to_occupy","already_occupied"]', FALSE, 1),
    ('consumer', 'tank_type',    'Tank Type',          'dropdown',
     '["plastic","steel","fiber","concrete"]',                       FALSE, 2),
    ('consumer', 'tank_age',     'Tank Age (years)',   'number',
     NULL,                                                           FALSE, 3),
    ('partner',  'partner_type', 'Partner Type',       'dropdown',
     '["plumber","contractor","builder","electrician","interior_designer"]', FALSE, 1),
    ('partner',  'work_area',    'Work Area',           'text',
     NULL,                                                           FALSE, 2);

-- ─── 3. contacts ──────────────────────────────────────────────────────────────
CREATE TABLE contacts (
    id              BIGSERIAL    PRIMARY KEY,
    contact_type    VARCHAR(10)  NOT NULL DEFAULT 'consumer'
                    CHECK (contact_type IN ('consumer', 'partner')),
    name            VARCHAR(150),
    phone           VARCHAR(20)  NOT NULL,
    -- Last 10 digits of phone; partial unique index below prevents duplicates for live contacts
    phone_key       VARCHAR(10)  NOT NULL DEFAULT '',
    phone_alt       VARCHAR(20),
    district        VARCHAR(80),
    address         VARCHAR(255),
    assigned_to     BIGINT       REFERENCES employees(id)      ON DELETE SET NULL,
    created_by      BIGINT       REFERENCES employees(id)      ON DELETE SET NULL,
    referred_by_id  BIGINT       REFERENCES contacts(id)       ON DELETE SET NULL,
    lead_source_id  INT          REFERENCES lead_sources(id)   ON DELETE SET NULL,
    -- Set when this contact was created by converting a lead
    lead_id         BIGINT       REFERENCES leads(id)          ON DELETE SET NULL,
    -- FK to branches (replaces the old VARCHAR enum column)
    branch_id       BIGINT       REFERENCES branches(id)       ON DELETE SET NULL,
    notes           TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

-- Partial unique: soft-deleted contacts don't block re-registration with the same phone
CREATE UNIQUE INDEX uq_contacts_phone_key_active ON contacts(phone_key)
    WHERE deleted_at IS NULL;

CREATE INDEX idx_contacts_type          ON contacts(contact_type);
CREATE INDEX idx_contacts_assigned      ON contacts(assigned_to);
CREATE INDEX idx_contacts_branch        ON contacts(branch_id);
CREATE INDEX idx_contacts_district      ON contacts(district);
CREATE INDEX idx_contacts_created_by    ON contacts(created_by);
CREATE INDEX idx_contacts_phone_key     ON contacts(phone_key);
-- Composite for listing active contacts sorted by newest
CREATE INDEX idx_contacts_live          ON contacts(deleted_at, created_at DESC, id DESC);
-- Composite for "my contacts" queries (assigned_to + live filter)
CREATE INDEX idx_contacts_live_assigned ON contacts(assigned_to, deleted_at, created_at DESC, id DESC);
-- Referral tree lookups on active contacts only
CREATE INDEX idx_contacts_referrer      ON contacts(referred_by_id) WHERE deleted_at IS NULL;

-- ─── 4. consumer_profiles ─────────────────────────────────────────────────────
-- Profile answers are stored in JSONB so the structure is fully tenant-configurable.
-- For ventino: data = {"house_stage": "occupied", "tank_type": "plastic", "tank_age": 3}
-- Another tenant's data might look completely different — no code change needed.
CREATE TABLE consumer_profiles (
    id          BIGSERIAL PRIMARY KEY,
    contact_id  BIGINT    NOT NULL UNIQUE REFERENCES contacts(id) ON DELETE CASCADE,
    data        JSONB     NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_consumer_profiles_contact ON consumer_profiles(contact_id);

-- ─── 5. partner_profiles ──────────────────────────────────────────────────────
-- Same JSONB pattern as consumer_profiles.
-- For ventino: data = {"partner_type": "plumber", "work_area": "malappuram"}
CREATE TABLE partner_profiles (
    id          BIGSERIAL PRIMARY KEY,
    contact_id  BIGINT    NOT NULL UNIQUE REFERENCES contacts(id) ON DELETE CASCADE,
    data        JSONB     NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_partner_profiles_contact ON partner_profiles(contact_id);

COMMIT;
