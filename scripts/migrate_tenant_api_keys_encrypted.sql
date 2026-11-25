-- =============================================================================
-- migrate_tenant_api_keys_encrypted.sql
-- Adds an encrypted copy of each raw API key so admins can copy it again
-- from Settings → Integrations.
--
-- HOW TO USE:
--   Run ONCE against the ogera_crm database (public schema).
--   Existing keys keep raw_key_encrypted = NULL; they cannot be revealed.
--   Generate a new key in the UI to get a copyable one.
--   Requires WHATSAPP_ENCRYPTION_KEY to be set on the backend before creating keys.
-- =============================================================================

ALTER TABLE public.tenant_api_keys
    ADD COLUMN IF NOT EXISTS raw_key_encrypted TEXT NULL;
