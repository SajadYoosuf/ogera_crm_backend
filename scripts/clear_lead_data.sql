-- =============================================================================
-- clear_lead_data.sql
-- Permanently deletes every row in the leads table for ONE tenant schema.
--
-- WARNING — IRREVERSIBLE:
--   This is a hard DELETE, not the soft-delete the app uses. Once committed,
--   the lead rows cannot be recovered from the app. Take a backup first.
--
-- WHAT IT DOES NOT TOUCH:
--   - contacts: contacts that were converted from a lead are KEPT. Their
--     lead_id is set to NULL first so the foreign key does not block the delete.
--   - lead_statuses, lead_sources, branches, employees: configuration, not lead data.
--
-- HOW TO USE (Postgres GUI or psql, one tenant at a time):
--   1. Set the tenant schema (replace ventino if needed):
--        SET search_path TO ventino;
--   2. Run Step 1 on its own and check the counts.
--   3. Only if the counts are what you expect, run the whole script.
--      It runs inside a transaction: it COMMITs at the end. To abort instead,
--      replace the final COMMIT with ROLLBACK.
-- =============================================================================

-- Step 1: preview. Run this alone first to see what will be removed.
SELECT
    (SELECT COUNT(*) FROM leads)                              AS leads_to_delete,
    (SELECT COUNT(*) FROM contacts WHERE lead_id IS NOT NULL) AS contacts_to_unlink;

-- Step 2: delete, all or nothing.
BEGIN;

-- Unlink converted contacts so the foreign key does not block the delete.
UPDATE contacts
SET    lead_id = NULL
WHERE  lead_id IS NOT NULL;

-- Remove every lead, including soft-deleted ones.
DELETE FROM leads;

COMMIT;

-- Step 3 (optional): confirm the table is empty.
SELECT COUNT(*) AS leads_remaining FROM leads;
