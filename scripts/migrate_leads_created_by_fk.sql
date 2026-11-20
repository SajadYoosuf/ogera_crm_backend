-- =============================================================================
-- migrate_leads_created_by_fk.sql
-- Connects leads.created_by to employees.id with a real foreign key.
--
-- WHY:
--   created_by says which employee entered a lead. Until now it was a plain
--   BIGINT with no link to employees, so a typo or a deleted employee could
--   leave a lead pointing at nobody. With the FK, the database itself rejects
--   a created_by that is not a real employee.
--
-- HOW TO USE (run once per tenant schema):
--   SET search_path TO <schema_name>;
--   \i scripts/migrate_leads_created_by_fk.sql
--
-- SAFE TO RE-RUN: the constraint is only added if it is missing, and the
-- index uses IF NOT EXISTS.
-- =============================================================================

-- Step 1: clear created_by values that do not match any employee.
-- They would make the FK fail to create. The old code never set this column
-- to a real employee id, so these values carry no useful information.
UPDATE leads
SET    created_by = NULL
WHERE  created_by IS NOT NULL
  AND  created_by NOT IN (SELECT id FROM employees);

-- Step 2: add the foreign key (skipped if it already exists).
-- ON DELETE SET NULL: if an employee is deleted, their leads stay, with
-- created_by empty, instead of blocking the delete.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM   pg_constraint
        WHERE  conname = 'leads_created_by_fkey'
          AND  conrelid = 'leads'::regclass
    ) THEN
        ALTER TABLE leads
            ADD CONSTRAINT leads_created_by_fkey
            FOREIGN KEY (created_by) REFERENCES employees(id) ON DELETE SET NULL;
    END IF;
END
$$;

-- Step 3: index for "leads created by this employee" lookups.
CREATE INDEX IF NOT EXISTS idx_leads_created_by ON leads(created_by);
