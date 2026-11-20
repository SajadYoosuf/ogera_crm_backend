-- =============================================================================
-- migrate_lead_statuses_table.sql
-- Creates lead_statuses (the DB-backed source of truth for the Lead status
-- dropdown — see models/lead_status.py) and seeds it, then remaps existing
-- leads.status values onto the new vocabulary.
--
-- WHY:
--   GET /leads/statuses used to return a hardcoded 5-value list. The dropdown
--   now needs these 9 values instead, as a real table so it can be managed
--   without a backend deploy.
--
-- HOW TO USE (run once per tenant schema):
--   SET search_path TO <schema_name>;
--   \i scripts/migrate_lead_statuses_table.sql
--
-- SAFE TO RE-RUN: CREATE TABLE/INDEX use IF NOT EXISTS, and the seed INSERT
-- uses ON CONFLICT DO NOTHING.
--
-- ⚠ REVIEW STEP 3 BEFORE RUNNING IT — read the mapping comment there.
-- =============================================================================

-- Step 1: create the table.
CREATE TABLE IF NOT EXISTS lead_statuses (
    id          BIGSERIAL PRIMARY KEY,
    slug        VARCHAR(30) NOT NULL UNIQUE,
    name        VARCHAR(60) NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Step 2: seed the 9 statuses, in the given order.
-- "converted" is deliberately NOT one of these rows — it stays a special
-- terminal value set only by POST /leads/{id}/convert.
INSERT INTO lead_statuses (slug, name, sort_order) VALUES
    ('new',                 'New',                 0),
    ('attempt_to_contact',  'Attempt to Contact',  1),
    ('contact_in_future',   'Contact in Future',   2),
    ('contacted',           'Contacted',           3),
    ('junk_lead',           'Junk Lead',           4),
    ('lost_lead',           'Lost Lead',           5),
    ('not_contacted',       'Not Contacted',       6),
    ('pre_qualified',       'Pre-Qualified',       7),
    ('not_qualified',       'Not Qualified',       8)
ON CONFLICT (slug) DO NOTHING;

-- Step 3: remap existing leads.status values onto the new vocabulary.
-- The OLD 5-value set was: new, contacted, qualified, converted, lost.
-- "new", "contacted" and "converted" are unchanged. The other two are a
-- judgment call — REVIEW before running, change the mapping if your team
-- means something different by "qualified" / "lost":
--   qualified -> pre_qualified  (closest existing meaning: vetted, not yet won)
--   lost      -> lost_lead      (closest existing meaning: dead lead)
UPDATE leads SET status = 'pre_qualified' WHERE status = 'qualified';
UPDATE leads SET status = 'lost_lead'     WHERE status = 'lost';

-- Step 4 (optional, informational): confirm no lead is left with a status
-- that isn't either "converted" or an active lead_statuses slug. Should
-- return zero rows after Step 3.
SELECT id, status FROM leads
WHERE status <> 'converted'
  AND status NOT IN (SELECT slug FROM lead_statuses WHERE is_active);
