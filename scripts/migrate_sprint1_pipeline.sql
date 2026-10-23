-- Sprint 1: Lead Pipeline Task System Redesign
-- Run once against the production/staging database.
-- Safe to re-run: uses IF NOT EXISTS / IF EXISTS guards.

-- 1. Add created_by to followup_schedule
--    NULL for rows created before this migration. The creator-only reschedule rule is only
--    enforced when created_by IS NOT NULL, so existing rows remain freely reschedulable.
ALTER TABLE followup_schedule
    ADD COLUMN IF NOT EXISTS created_by BIGINT NULL,
    ADD CONSTRAINT IF NOT EXISTS fk_followup_created_by
        FOREIGN KEY (created_by) REFERENCES employees(id) ON DELETE SET NULL;

-- 2. Create the followup_edit_log audit table
CREATE TABLE IF NOT EXISTS followup_edit_log (
    id          BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    followup_id BIGINT          NOT NULL,
    contact_id  BIGINT          NULL,
    changed_by  BIGINT          NOT NULL,
    changed_at  DATETIME        NOT NULL DEFAULT NOW(),
    field_name  VARCHAR(40)     NOT NULL,
    old_value   TEXT            NULL,
    new_value   TEXT            NULL,
    CONSTRAINT fk_fol_edit_followup FOREIGN KEY (followup_id) REFERENCES followup_schedule(id),
    CONSTRAINT fk_fol_edit_contact  FOREIGN KEY (contact_id)  REFERENCES contacts(id),
    CONSTRAINT fk_fol_edit_changer  FOREIGN KEY (changed_by)  REFERENCES employees(id),
    INDEX idx_fol_edit_followup (followup_id),
    INDEX idx_fol_edit_contact  (contact_id)
);

-- 3. Seed meeting and email activity types (if not already present)
INSERT IGNORE INTO enum_values (category, value, label, is_active, display_order)
SELECT 'activity_type', 'meeting', 'Meeting', 1,
       COALESCE((SELECT MAX(display_order) FROM enum_values WHERE category = 'activity_type'), 0) + 1
WHERE NOT EXISTS (SELECT 1 FROM enum_values WHERE category = 'activity_type' AND value = 'meeting');

INSERT IGNORE INTO enum_values (category, value, label, is_active, display_order)
SELECT 'activity_type', 'email', 'Email', 1,
       COALESCE((SELECT MAX(display_order) FROM enum_values WHERE category = 'activity_type'), 0) + 2
WHERE NOT EXISTS (SELECT 1 FROM enum_values WHERE category = 'activity_type' AND value = 'email');
