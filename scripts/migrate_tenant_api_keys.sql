-- =============================================================================
-- migrate_tenant_api_keys.sql
-- Creates the tenant_api_keys table in the PUBLIC schema.
--
-- HOW TO USE:
--   Run this ONCE against the ogera_crm database (not inside a tenant schema).
--   In psql: \i scripts/migrate_tenant_api_keys.sql
--   In pgAdmin: open and run as-is (uses public schema explicitly).
--
-- WHY THIS TABLE?
--   Inbound lead endpoints (website forms, Google Sheet macros, Zapier automations)
--   are public — they don't require a logged-in employee JWT. Instead each tenant
--   has one or more API keys stored here. The calling script includes the key in
--   the X-Api-Key header; the backend hashes it and checks this table to identify
--   the tenant and authorize the request.
-- =============================================================================

-- key_hash stores the sha256 hex digest of the raw key — we never store plaintext keys.
CREATE TABLE IF NOT EXISTS public.tenant_api_keys (
    id         SERIAL        PRIMARY KEY,
    tenant_id  INTEGER       NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    key_hash   VARCHAR(64)   NOT NULL,
    label      VARCHAR(60)   NOT NULL DEFAULT 'default',
    is_active  BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- Speed up the two most common lookups: list keys for a tenant, verify a key hash.
CREATE INDEX IF NOT EXISTS idx_tenant_api_keys_tenant ON public.tenant_api_keys(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_api_keys_hash   ON public.tenant_api_keys(key_hash);
