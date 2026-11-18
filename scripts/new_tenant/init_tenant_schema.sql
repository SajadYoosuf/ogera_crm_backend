-- =============================================================================
-- init_tenant_schema.sql
-- Run once to bootstrap a brand-new tenant (company) in the database.
--
-- HOW TO USE:
--   1. Open pgAdmin (or psql) connected to the ogera_crm database.
--   2. Find-and-replace the word  ventino  with the real schema name.
--      Schema name rules: lowercase letters, digits and underscores only;
--      must start with a letter; max 63 characters.  Example: acme_corp
--   3. Add the new tenant row to public.tenants first (if not already done):
--         INSERT INTO public.tenants (name, schema_name)
--         VALUES ('Acme Corp', 'acme_corp');
--      Note the id it gets — you need it in Step 8 below.
--   4. Run this entire script inside a BEGIN / COMMIT block (pgAdmin wraps
--      it automatically; in psql wrap manually with BEGIN; ... COMMIT;).
--   5. After running, insert the first admin employee via the API:
--         POST /employees  { "name": "...", "email": "...", "role": "admin", ... }
--      That endpoint also adds the email to public.employee_tenant_map for you.
--      Or insert manually — see the commented INSERT at the bottom of this file.
-- =============================================================================

BEGIN;

-- ── 1. Create the schema ──────────────────────────────────────────────────────
-- Each tenant lives in its own PostgreSQL schema so their tables are
-- completely isolated from every other company.

CREATE SCHEMA IF NOT EXISTS ventino;

-- ── 2. Point all table statements at this schema ──────────────────────────────
SET search_path TO ventino;


-- ── 3. roles ──────────────────────────────────────────────────────────────────
-- Defines what each employee is allowed to do.
-- slug is the machine-readable name used in permission checks.

CREATE TABLE IF NOT EXISTS roles (
    id         SERIAL       PRIMARY KEY,
    slug       VARCHAR(40)  NOT NULL UNIQUE,
    name       VARCHAR(80)  NOT NULL UNIQUE,
    is_active  SMALLINT     NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Seed the 7 built-in roles.
-- ON CONFLICT DO NOTHING makes this safe to re-run.
INSERT INTO roles (slug, name) VALUES
    ('admin',           'Admin'),
    ('manager',         'Manager'),
    ('team_lead',       'Team Lead'),
    ('sales_executive', 'Sales Executive'),
    ('tele_caller',     'Tele Caller'),
    ('field_executive', 'Field Executive'),
    ('support',         'Support')
ON CONFLICT (slug) DO NOTHING;


-- ── 4. employees ──────────────────────────────────────────────────────────────
-- One row per staff member of this company.
-- password_hash stores an argon2 hash — never the plain password.
-- deleted_at is used for soft-delete; NULL means the account is not deleted.

CREATE TABLE IF NOT EXISTS employees (
    id            BIGSERIAL    PRIMARY KEY,
    name          VARCHAR(120) NOT NULL,
    email         VARCHAR(180) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role_id       INT          NOT NULL REFERENCES roles(id),
    phone         VARCHAR(20)  UNIQUE,
    is_active     SMALLINT     NOT NULL DEFAULT 1,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    deleted_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_employees_email     ON employees (lower(email));
CREATE INDEX IF NOT EXISTS idx_employees_is_active ON employees (is_active);


-- ── 5. password_reset_codes ───────────────────────────────────────────────────
-- Stores HMAC hashes of the 6-digit OTP codes sent during forgot-password.
-- The actual code is never stored here — only its keyed hash.
--
-- consumed_at is set when:
--   - the code is successfully verified (step 2 of the reset flow)
--   - a new code is requested (old one is invalidated)
-- expires_at is set when the code is generated (NOW + RESET_CODE_TTL_MINUTES).

CREATE TABLE IF NOT EXISTS password_reset_codes (
    id          BIGSERIAL    PRIMARY KEY,
    employee_id BIGINT       NOT NULL REFERENCES employees(id),
    code_hash   VARCHAR(64)  NOT NULL,
    attempts    SMALLINT     NOT NULL DEFAULT 0,
    expires_at  TIMESTAMPTZ  NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    consumed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_prc_employee_id  ON password_reset_codes (employee_id);
CREATE INDEX IF NOT EXISTS idx_prc_consumed_at  ON password_reset_codes (consumed_at);


-- ── 6. lead_sources ───────────────────────────────────────────────────────────
-- Where a lead came from: WhatsApp, Website, Referral, etc.
-- Used when recording a new contact / lead.

CREATE TABLE IF NOT EXISTS lead_sources (
    id         SERIAL       PRIMARY KEY,
    name       VARCHAR(100) NOT NULL UNIQUE,
    is_active  SMALLINT     NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);


-- ── 7. Register the first admin employee in the public lookup ─────────────────
-- public.employee_tenant_map maps each employee email to their tenant id so the
-- login and password-reset flows know which schema to open.
--
-- Replace:
--   'admin@example.com'  with the first admin's real email
--   1                    with the actual id from public.tenants for this tenant

-- INSERT INTO public.employee_tenant_map (email, tenant_id)
-- VALUES ('admin@example.com', 1)
-- ON CONFLICT (email) DO NOTHING;

-- After uncommenting the line above and running it, also insert the employee row:
--
-- INSERT INTO ventino.employees (name, email, password_hash, role_id)
-- VALUES (
--     'Admin User',
--     'admin@example.com',
--     '$argon2id$...',   -- generate with: python -c "from passlib.hash import argon2; print(argon2.hash('yourpassword'))"
--     (SELECT id FROM ventino.roles WHERE slug = 'admin')
-- );
--
-- Or use the API endpoint (recommended — it handles the public lookup too):
--   POST /employees  { "name": "Admin User", "email": "admin@example.com",
--                      "role": "admin", "password": "..." }

COMMIT;

-- ── Verify ────────────────────────────────────────────────────────────────────
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'ventino'
ORDER BY table_name;

SELECT slug, name FROM ventino.roles ORDER BY id;
