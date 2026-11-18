-- Live DB migration — run once on 187.127.150.170:5432 / ogera_crm
-- Schema: ventino (tenant) + public (lookup)
-- Run this in psql or pgAdmin BEFORE starting the new backend.

BEGIN;

-- ── 1. ventino.roles: add slug, is_active, updated_at ────────────────────────
ALTER TABLE ventino.roles ADD COLUMN IF NOT EXISTS slug        VARCHAR(40);
ALTER TABLE ventino.roles ADD COLUMN IF NOT EXISTS is_active   SMALLINT NOT NULL DEFAULT 1;
ALTER TABLE ventino.roles ADD COLUMN IF NOT EXISTS updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Auto-generate slugs from existing role names (e.g. "Sales Executive" → "sales_executive")
UPDATE ventino.roles
SET slug = LOWER(REPLACE(REPLACE(name, ' ', '_'), '-', '_'))
WHERE slug IS NULL;

ALTER TABLE ventino.roles ALTER COLUMN slug SET NOT NULL;

-- Add unique constraint only if it doesn't already exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_roles_slug' AND conrelid = 'ventino.roles'::regclass
    ) THEN
        ALTER TABLE ventino.roles ADD CONSTRAINT uq_roles_slug UNIQUE (slug);
    END IF;
END $$;

-- ── 2. ventino.employees: add updated_at, deleted_at; fix is_active type ─────
ALTER TABLE ventino.employees ADD COLUMN IF NOT EXISTS updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE ventino.employees ADD COLUMN IF NOT EXISTS deleted_at  TIMESTAMPTZ;

-- Change is_active from boolean → smallint (0/1) to match the Python model
ALTER TABLE ventino.employees
    ALTER COLUMN is_active TYPE SMALLINT USING (is_active::int);
ALTER TABLE ventino.employees
    ALTER COLUMN is_active SET DEFAULT 1;

COMMIT;

-- ── Verify ────────────────────────────────────────────────────────────────────
SELECT id, name, slug, is_active FROM ventino.roles ORDER BY id;
SELECT id, name, email, role_id, is_active, updated_at, deleted_at
FROM ventino.employees ORDER BY id;
