-- =============================================================================
-- migrate_leads_phone_unique.sql
-- Normalizes leads.phone_key and adds a partial unique index on it, mirroring
-- the constraint contacts already has (uq_contacts_phone_key_active).
--
-- WHY:
--   The app-level duplicate check (services/lead_service.check_duplicate) can
--   race under concurrent requests. A real DB constraint is the backstop.
--   The index is partial (WHERE deleted_at IS NULL) so soft-deleted leads
--   don't block a new lead from re-using the same phone number.
--
-- HOW TO USE (run once per tenant schema):
--   SET search_path TO <schema_name>;
--   \i scripts/migrate_leads_phone_unique.sql
--
-- IMPORTANT — READ BEFORE RUNNING:
--   Run Steps 1–2 first. Step 2 lists any LIVE leads that would collide once
--   normalized. If it returns rows, decide per group which lead to keep
--   (e.g. soft-delete the older/emptier duplicate: UPDATE leads SET deleted_at
--   = now() WHERE id = <id>) BEFORE running Step 3, or Step 3 will fail with a
--   "duplicate key" error.
-- =============================================================================

-- Step 1: recompute phone_key as the last 10 digits of phone, for every lead
-- whose stored phone_key doesn't already match (covers rows where phone_key
-- was never set, or phone was edited directly in the DB).
UPDATE leads
SET    phone_key = RIGHT(REGEXP_REPLACE(phone, '[^0-9]', '', 'g'), 10)
WHERE  phone IS NOT NULL
  AND  (phone_key IS DISTINCT FROM RIGHT(REGEXP_REPLACE(phone, '[^0-9]', '', 'g'), 10));

-- Step 2: show any live leads that would collide on phone_key after
-- normalizing. Review this output — if it's empty, skip straight to Step 3.
SELECT phone_key, COUNT(*) AS cnt, array_agg(id ORDER BY created_at) AS lead_ids
FROM   leads
WHERE  phone_key IS NOT NULL AND phone_key <> '' AND deleted_at IS NULL
GROUP BY phone_key
HAVING COUNT(*) > 1;

-- Step 3: add the partial unique index (run only after Step 2 returns no rows).
CREATE UNIQUE INDEX IF NOT EXISTS uq_leads_phone_key_active ON leads(phone_key)
WHERE deleted_at IS NULL AND phone_key IS NOT NULL AND phone_key <> '';
