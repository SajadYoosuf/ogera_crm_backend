-- Sprint 2: Contact creation auto follow-up (48h rule)
-- Run this once against MySQL before deploying the Sprint 2 backend.
-- The only schema change is adding the AUTO_FOLLOWUP_HOURS config constant — no table changes needed.
-- The followup_schedule table already has all required columns from Sprint 1 (created_by, priority, title).
-- This script is a no-op if re-run (safe to execute multiple times).

-- Verify the followup_schedule table has the Sprint 1 columns before proceeding:
-- SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
--   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'followup_schedule'
--   AND COLUMN_NAME IN ('created_by', 'priority', 'title');
-- Expected: 3 rows. If < 3, run migrate_sprint1_pipeline.sql first.
SELECT 'Sprint 2 migration: no schema changes required. followup_schedule columns assumed from Sprint 1.' AS status;
