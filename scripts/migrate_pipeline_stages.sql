-- Migration: rename stage_types → pipeline_stages
--
-- What this does:
--   1. Renames the table that stores pipeline stage definitions.
--   2. MySQL automatically updates the foreign key in lead_stage_log to point
--      to the new table name, so no manual FK drop/recreate is needed.
--
-- Run once on the production database. Safe to re-run (RENAME TABLE fails
-- if the new name already exists, so it will not silently double-rename).
--
-- Usage:
--   mysql -u ogera_crm -p ogera_crm < scripts/migrate_pipeline_stages.sql

RENAME TABLE stage_types TO pipeline_stages;
