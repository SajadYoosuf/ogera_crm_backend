-- Migration: activity_log + followup_schedule
-- Splits the single lead_activities table into two focused tables.
-- Run once on a production MySQL database.
-- Safe to run multiple times: CREATE TABLE IF NOT EXISTS, INSERT IGNORE.

-- ──────────────────────────────────────────────────────────────────────────────
-- 1.  Create followup_schedule
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS followup_schedule (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    contact_id  BIGINT UNSIGNED NOT NULL,
    assigned_to INT NOT NULL,
    due_at      DATETIME NOT NULL,
    status      VARCHAR(10) NOT NULL DEFAULT 'pending',  -- pending / done / skipped
    channel     VARCHAR(15) NOT NULL DEFAULT 'call',     -- call / visit / whatsapp
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_fs_contact  FOREIGN KEY (contact_id) REFERENCES contacts(id),
    CONSTRAINT fk_fs_employee FOREIGN KEY (assigned_to) REFERENCES employees(id),
    INDEX idx_followup_schedule_contact_id (contact_id),
    INDEX idx_followup_schedule_assigned   (assigned_to, status, due_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ──────────────────────────────────────────────────────────────────────────────
-- 2.  Create activity_log
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS activity_log (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    contact_id    BIGINT UNSIGNED NOT NULL,
    done_by       INT NOT NULL,
    activity_type VARCHAR(20) NOT NULL,
    outcome       VARCHAR(20) DEFAULT NULL,
    notes         TEXT DEFAULT NULL,
    followup_id   BIGINT UNSIGNED DEFAULT NULL,
    latitude      DECIMAL(9,6) DEFAULT NULL,
    longitude     DECIMAL(9,6) DEFAULT NULL,
    logged_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_al_contact  FOREIGN KEY (contact_id)  REFERENCES contacts(id),
    CONSTRAINT fk_al_employee FOREIGN KEY (done_by)     REFERENCES employees(id),
    CONSTRAINT fk_al_followup FOREIGN KEY (followup_id) REFERENCES followup_schedule(id),
    INDEX idx_activity_log_contact_id  (contact_id),
    INDEX idx_activity_log_followup_id (followup_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ──────────────────────────────────────────────────────────────────────────────
-- 3.  Back-fill pending lead_activities → followup_schedule
-- ──────────────────────────────────────────────────────────────────────────────
INSERT IGNORE INTO followup_schedule (contact_id, assigned_to, due_at, status, channel, created_at)
SELECT
    la.contact_id,
    COALESCE(la.assigned_to, la.created_by)             AS assigned_to,
    COALESCE(
        CASE
            WHEN la.scheduled_time IS NOT NULL
                THEN CONCAT(la.scheduled_date, ' ', la.scheduled_time)
            ELSE CONCAT(la.scheduled_date, ' 00:00:00')
        END,
        la.created_at
    )                                                    AS due_at,
    'done'                                               AS status,  -- historical pending rows are done by definition
    la.type                                              AS channel,
    la.created_at
FROM lead_activities la
WHERE la.scheduled_date IS NOT NULL;

-- ──────────────────────────────────────────────────────────────────────────────
-- 4.  Back-fill done log entries → activity_log
-- ──────────────────────────────────────────────────────────────────────────────
INSERT IGNORE INTO activity_log (contact_id, done_by, activity_type, notes, logged_at)
SELECT
    la.contact_id,
    la.created_by   AS done_by,
    la.type         AS activity_type,
    la.notes,
    COALESCE(la.completed_at, la.created_at) AS logged_at
FROM lead_activities la
WHERE la.scheduled_date IS NULL;

-- ──────────────────────────────────────────────────────────────────────────────
-- NOTE: lead_activities is NOT dropped here.
-- Verify data integrity, then drop manually once confirmed:
--   DROP TABLE lead_activities;
-- ──────────────────────────────────────────────────────────────────────────────
