-- =============================================================================
-- migrate_leads_branch.sql
-- Adds branch_id to leads — a FK to the branches table, same pattern contacts
-- already uses.
--
-- WHY:
--   Leads had no branch_id column. Manual lead creation (and any employee-run
--   import) should default to the creating employee's own branch, reselectable
--   in the form. The webhook/inbound channels (website, Meta Ads sheet,
--   WhatsApp) have no logged-in employee, so their leads stay branch-less.
--
-- HOW TO USE (run once per tenant schema):
--   SET search_path TO <schema_name>;
--   \i scripts/migrate_leads_branch.sql
--
-- SAFE TO RE-RUN: the column and FK are only added if missing, and the index
-- uses IF NOT EXISTS.
-- =============================================================================

-- Step 1: add the column (nullable — existing leads have no branch recorded).
ALTER TABLE leads ADD COLUMN IF NOT EXISTS branch_id BIGINT NULL;

-- Step 2: add the foreign key (skipped if it already exists).
-- ON DELETE SET NULL: if a branch is removed, leads stay, with branch_id empty,
-- instead of blocking the delete.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM   pg_constraint
        WHERE  conname = 'leads_branch_id_fkey'
          AND  conrelid = 'leads'::regclass
    ) THEN
        ALTER TABLE leads
            ADD CONSTRAINT leads_branch_id_fkey
            FOREIGN KEY (branch_id) REFERENCES branches(id) ON DELETE SET NULL;
    END IF;
END
$$;

-- Step 3: index for "leads in this branch" lookups and filters.
CREATE INDEX IF NOT EXISTS idx_leads_branch ON leads(branch_id);
