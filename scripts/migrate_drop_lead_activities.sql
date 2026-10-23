-- Drops the unused lead_activities table. Activity history now lives in activity_log.
-- Run only after confirming the table is empty or its rows are not needed:
--   SELECT COUNT(*) FROM lead_activities;
-- Not run automatically.

DROP TABLE IF EXISTS lead_activities;
