-- Migration: clean up activity_log and followup_schedule schema.
-- Safe to run on existing databases (additive + default-safe changes).

-- activity_log: remove GPS columns, add title
ALTER TABLE activity_log
    DROP COLUMN IF EXISTS latitude,
    DROP COLUMN IF EXISTS longitude,
    ADD COLUMN IF NOT EXISTS title VARCHAR(200) NULL AFTER id;

-- followup_schedule: new status values, add priority, make contact_id and channel nullable,
-- remove whatsapp_reminder
ALTER TABLE followup_schedule
    ADD COLUMN IF NOT EXISTS title    VARCHAR(200) NULL        AFTER id,
    ADD COLUMN IF NOT EXISTS priority VARCHAR(10)  NOT NULL DEFAULT 'medium' AFTER status,
    MODIFY COLUMN contact_id BIGINT NULL,
    MODIFY COLUMN channel    VARCHAR(15) NULL,
    MODIFY COLUMN status     VARCHAR(15) NOT NULL DEFAULT 'todo';

-- Migrate old status values to new ones
UPDATE followup_schedule SET status = 'todo'      WHERE status = 'pending';
UPDATE followup_schedule SET status = 'completed' WHERE status = 'done';
UPDATE followup_schedule SET status = 'completed' WHERE status = 'skipped';

-- Remove whatsapp_reminder if it was added in a prior migration
ALTER TABLE followup_schedule
    DROP COLUMN IF EXISTS whatsapp_reminder;
