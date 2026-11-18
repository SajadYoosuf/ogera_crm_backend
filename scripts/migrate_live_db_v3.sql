-- Live DB migration v3 — run once on 187.127.150.170:5432 / ogera_crm
-- Run each statement individually in pgAdmin if you hit errors.
-- Run AFTER migrate_live_db_v2.sql is complete.

BEGIN;

-- ── 1. Create public.tenants ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.tenants (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(120) NOT NULL,
    schema_name VARCHAR(63)  NOT NULL UNIQUE,
    logo_url    TEXT,
    timezone    VARCHAR(50)  NOT NULL DEFAULT 'Asia/Kolkata',
    is_active   SMALLINT     NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── 2. Seed tenants from existing employee_tenant_map data ───────────────────
-- Inserts one row per unique schema_name already in employee_tenant_map.
-- Name defaults to the schema_name — update it manually after migration.
INSERT INTO public.tenants (name, schema_name)
SELECT DISTINCT schema_name, schema_name
FROM public.employee_tenant_map
ON CONFLICT (schema_name) DO NOTHING;

-- ── 3. Add FK column tenant_id to employee_tenant_map ────────────────────────
ALTER TABLE public.employee_tenant_map
    ADD COLUMN IF NOT EXISTS tenant_id_new INT;

-- Fill tenant_id_new by matching schema_name
UPDATE public.employee_tenant_map etm
SET tenant_id_new = t.id
FROM public.tenants t
WHERE t.schema_name = etm.schema_name;

-- Make it NOT NULL now that all rows are filled
ALTER TABLE public.employee_tenant_map
    ALTER COLUMN tenant_id_new SET NOT NULL;

-- Drop the old tenant_id column (was just a plain int, no FK)
ALTER TABLE public.employee_tenant_map
    DROP COLUMN IF EXISTS tenant_id;

-- Rename to tenant_id
ALTER TABLE public.employee_tenant_map
    RENAME COLUMN tenant_id_new TO tenant_id;

-- Add FK constraint
ALTER TABLE public.employee_tenant_map
    ADD CONSTRAINT fk_etm_tenant
    FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);

-- ── 4. Remove schema_name from employee_tenant_map ───────────────────────────
-- schema_name now lives only in public.tenants.
ALTER TABLE public.employee_tenant_map
    DROP COLUMN IF EXISTS schema_name;

COMMIT;

-- ── Verify ────────────────────────────────────────────────────────────────────
SELECT * FROM public.tenants;
SELECT email, tenant_id FROM public.employee_tenant_map;
