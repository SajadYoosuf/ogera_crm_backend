-- Migration: three-type DB-driven lead pipeline
-- Run once on a production MySQL database.
-- The Python layer already uses stage_types.id (FK) instead of an ENUM string column.
-- This script creates the new lead_stage_log table, adds contact_type to
-- stage_types, removes the old slug-unique constraint, seeds all 27 stage rows and
-- (optionally) back-fills existing lead_stages rows.
--
-- Safe to run multiple times: all INSERT statements use INSERT IGNORE.

-- ──────────────────────────────────────────────────────────────────────────────
-- 1.  Extend stage_types with contact_type
-- ──────────────────────────────────────────────────────────────────────────────
ALTER TABLE stage_types
    ADD COLUMN IF NOT EXISTS contact_type ENUM('consumer','partner','dealer') NOT NULL DEFAULT 'consumer' AFTER slug;

-- Drop the old unique constraint on slug alone (same slug can appear once per type)
ALTER TABLE stage_types DROP INDEX IF EXISTS uq_stage_types_slug;
-- Add composite unique
ALTER TABLE stage_types ADD UNIQUE KEY IF NOT EXISTS uq_stage_type_slug_per_type (slug, contact_type);

-- ──────────────────────────────────────────────────────────────────────────────
-- 2.  Create the new lead_stage_log table (replaces lead_stages for new records)
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lead_stage_log (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    contact_id      BIGINT UNSIGNED NOT NULL,
    stage_id        BIGINT NOT NULL,
    changed_by      INT NOT NULL,
    changed_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    loss_reason     VARCHAR(80)  DEFAULT NULL,
    loss_notes      TEXT         DEFAULT NULL,
    awareness_note  TEXT         DEFAULT NULL,
    CONSTRAINT fk_lsl_contact FOREIGN KEY (contact_id) REFERENCES contacts(id),
    CONSTRAINT fk_lsl_stage   FOREIGN KEY (stage_id)   REFERENCES stage_types(id),
    INDEX idx_lead_stage_log_contact_id (contact_id),
    INDEX idx_lead_stage_log_stage_id   (stage_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ──────────────────────────────────────────────────────────────────────────────
-- 3.  Seed the 27 stage rows  (9 per contact type)
-- ──────────────────────────────────────────────────────────────────────────────

-- Consumer pipeline
INSERT IGNORE INTO stage_types (slug, name, description, contact_type, `order`, is_active) VALUES
('new_lead',              'New Lead',               'Just entered. Not yet contacted.',                    'consumer', 1, 1),
('first_call',            'First Call',             'First call attempted. Waiting to reach them.',        'consumer', 2, 1),
('connected',             'Connected',              'Spoke to them. Qualify and decide next step.',        'consumer', 3, 1),
('aware',                 'Aware',                  'Needs education before hearing a price.',             'consumer', 4, 1),
('interested',            'Interested',             'Ready for demo or detailed info.',                    'consumer', 5, 1),
('quotation',             'Quotation',              'Quote sent. 3-day follow-up clock starts.',           'consumer', 6, 1),
('won',                   'Won',                    'Deal closed.',                                        'consumer', 7, 1),
('lost',                  'Lost',                   'Did not buy. Reason captured.',                       'consumer', 8, 1),
('after_sales_referring', 'After Sales / Referring','Post-sale support and referral tracking.',            'consumer', 9, 1);

-- Partner pipeline
INSERT IGNORE INTO stage_types (slug, name, description, contact_type, `order`, is_active) VALUES
('new_lead',          'New Lead',           'Just entered.',                              'partner', 1, 1),
('first_call',        'First Call',         'First contact made.',                        'partner', 2, 1),
('connected',         'Connected',          'Qualified partner prospect.',                'partner', 3, 1),
('demo_introduction', 'Demo / Introduction','Product demo scheduled or delivered.',       'partner', 4, 1),
('aware',             'Aware',              'Partner understands the product.',           'partner', 5, 1),
('interested',        'Interested',         'Partner ready to commit.',                   'partner', 6, 1),
('active',            'Active',             'Active partner.',                            'partner', 7, 1),
('referral',          'Referral',           'Partner sending referrals.',                 'partner', 8, 1),
('inactive',          'Inactive',           'Partner no longer active.',                  'partner', 9, 1);

-- Dealer pipeline
INSERT IGNORE INTO stage_types (slug, name, description, contact_type, `order`, is_active) VALUES
('new_lead',       'New Lead',        'Just entered.',                             'dealer', 1, 1),
('connected',      'Connected',       'Initial contact made.',                     'dealer', 2, 1),
('meeting_done',   'Meeting Done',    'In-person or video meeting completed.',     'dealer', 3, 1),
('terms_agreed',   'Terms Agreed',    'Commercial terms discussed and agreed.',    'dealer', 4, 1),
('sample_sent',    'Sample Sent',     'Product sample dispatched.',                'dealer', 5, 1),
('first_order',    'First Order',     'First order placed.',                       'dealer', 6, 1),
('placed',         'Placed',          'Order confirmed and in fulfilment.',        'dealer', 7, 1),
('active_dealer',  'Active Dealer',   'Recurring active dealer.',                  'dealer', 8, 1),
('inactive_dealer','Inactive Dealer', 'Dealer no longer ordering.',                'dealer', 9, 1);

-- ──────────────────────────────────────────────────────────────────────────────
-- 4.  Back-fill existing lead_stages rows into lead_stage_log  (optional)
--     Maps the old ENUM slug to the new consumer stage_type id.
--     Run only if you want historical data visible through the new API.
-- ──────────────────────────────────────────────────────────────────────────────
/*
INSERT IGNORE INTO lead_stage_log (id, contact_id, stage_id, changed_by, changed_at, loss_reason, loss_notes, awareness_note)
SELECT
    ls.id,
    ls.contact_id,
    st.id,
    ls.changed_by,
    ls.changed_at,
    ls.loss_reason,
    ls.loss_notes,
    ls.awareness_note
FROM lead_stages ls
JOIN stage_types st
    ON st.slug = ls.stage
    AND st.contact_type = COALESCE(
        (SELECT c.contact_type FROM contacts c WHERE c.id = ls.contact_id),
        'consumer'
    )
    AND st.is_active = 1;
*/
