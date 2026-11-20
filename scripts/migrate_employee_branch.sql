-- Migration: add branch_id to employees table in ventino schema
--
-- WHY THIS COLUMN?
--   Each employee belongs to one branch office (malappuram, kottakkal, perinthalmanna).
--   Contacts list shows "created_by_branch" so a manager can see at a glance
--   which branch office entered a lead.
--
-- PREREQUISITE:
--   Run scripts/migrate_contacts_pg.sql first so the branches table already exists.
--
-- HOW TO RUN:
--   psql -h 187.127.150.170 -U ogera_crm -d ogera_crm -f scripts/migrate_employee_branch.sql

BEGIN;
SET search_path TO ventino;

ALTER TABLE employees
    ADD COLUMN branch_id BIGINT REFERENCES branches(id) ON DELETE SET NULL;

CREATE INDEX idx_employees_branch ON employees(branch_id);

COMMIT;
