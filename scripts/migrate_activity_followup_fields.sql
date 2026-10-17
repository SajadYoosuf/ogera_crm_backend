-- Migration: add title/logged_at override/done_by fields to activity_log,
-- and title/whatsapp_reminder to followup_schedule.
-- Safe to run on an existing database (all changes are additive).

ALTER TABLE activity_log
    ADD COLUMN IF NOT EXISTS title VARCHAR(200) NULL AFTER id;

ALTER TABLE followup_schedule
    ADD COLUMN IF NOT EXISTS title VARCHAR(200) NULL AFTER id,
    ADD COLUMN IF NOT EXISTS whatsapp_reminder TINYINT(1) NOT NULL DEFAULT 0 AFTER notes;
