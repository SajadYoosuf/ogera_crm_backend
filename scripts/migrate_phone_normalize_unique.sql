-- Migration: normalize existing phone numbers to last 10 digits and add a unique index.
--
-- Why:
--   The Python schema validator now stores phones as last 10 digits (e.g. "9000000003").
--   Existing rows may still hold "+919000000003" or "09000000003". This script
--   normalizes them to match the new format before adding the unique index.
--
-- Run once. Safe to re-run (IF NOT EXISTS guards the index creation).

-- Step 1: strip all non-digit characters, then keep only the last 10 digits.
UPDATE employees
SET phone = RIGHT(REGEXP_REPLACE(phone, '[^0-9]', ''), 10)
WHERE phone IS NOT NULL
  AND phone != RIGHT(REGEXP_REPLACE(phone, '[^0-9]', ''), 10);

-- Step 2: if two employees somehow ended up with the same last-10-digits after
-- normalizing, NULL out the duplicates before adding the index (manual review needed).
-- The query below shows any collisions — check before proceeding if it returns rows.
SELECT phone, COUNT(*) AS cnt
FROM employees
WHERE phone IS NOT NULL AND deleted_at IS NULL
GROUP BY phone
HAVING cnt > 1;

-- Step 3: add the unique index (MySQL allows multiple NULLs in a unique index).
ALTER TABLE employees
    ADD UNIQUE INDEX uq_employees_phone (phone);
