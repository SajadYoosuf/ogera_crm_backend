-- =============================================================================
-- fix_lead_created_at.sql
-- Backfills created_at for leads that were imported and got today's timestamp.
--
-- HOW TO USE:
--   Run this in the tenant schema you want to fix, e.g.:
--       SET search_path TO ogera;
--       \i scripts/fix_lead_created_at.sql
--
-- SAFE TO RE-RUN: uses WHERE clauses that only target today's rows.
--
-- THREE STRATEGIES — pick the one(s) that match your data:
--   1. Auto: extract date from raw_payload->>'created_time' (Meta Ads / sheet imports)
--   2. Bulk: set all leads from a source to a known import date
--   3. Manual: set specific leads by id
-- =============================================================================

-- ── STEP 0: Preview ───────────────────────────────────────────────────────────
-- Run this first to see what you're working with. No changes made.
SELECT
    id,
    first_name,
    last_name,
    phone,
    source_id,
    status,
    created_at,
    -- Show raw_payload->>'created_time' if it exists (Meta Ads Unix epoch)
    raw_payload->>'created_time' AS meta_created_time
FROM leads
WHERE deleted_at IS NULL
  AND DATE(created_at) = CURRENT_DATE   -- only rows that got today's timestamp
ORDER BY id;


-- ── STEP 1 (Meta Ads): restore created_at from raw_payload->>'created_time' ──
-- Meta Ads leads have a Unix epoch timestamp in raw_payload.
-- This converts it back to a proper timestamp and applies it.
-- Only runs for leads created today whose raw_payload has 'created_time'.
--
-- UNCOMMENT and run when ready:

-- UPDATE leads
-- SET created_at = TO_TIMESTAMP((raw_payload->>'created_time')::BIGINT)
-- WHERE deleted_at IS NULL
--   AND DATE(created_at) = CURRENT_DATE
--   AND raw_payload ? 'created_time'
--   AND (raw_payload->>'created_time') ~ '^\d+$';   -- safety: only digits


-- ── STEP 2 (Bulk by source): set all leads from a channel to a known date ────
-- Use when you know that all leads from one source were added on a specific date.
-- For example, if you imported all JustDial leads on Nov 1, set their created_at
-- to Nov 1 at midnight so the display shows "1 Nov" instead of today.
--
-- Replace 6 with the correct source_id and change the date.
-- source_ids: 1=Walk-in, 2=Referral, 3=Meta Ads, 4=Website,
--             5=WhatsApp, 6=JustDial, 7=GMB, 8=Field Visit
--
-- UNCOMMENT and run when ready:

-- UPDATE leads
-- SET created_at = '2026-11-01 00:00:00+00'::TIMESTAMPTZ
-- WHERE deleted_at IS NULL
--   AND DATE(created_at) = CURRENT_DATE
--   AND source_id = 6;   -- replace with your source_id


-- ── STEP 3 (Manual): set specific leads by id or id range ───────────────────
-- Use when you know the exact date for specific leads.
-- You can list individual ids or give a range (e.g. id BETWEEN 1 AND 50).
--
-- UNCOMMENT and run when ready:

-- UPDATE leads
-- SET created_at = '2026-10-15 00:00:00+00'::TIMESTAMPTZ
-- WHERE id IN (1, 2, 3, 4, 5);   -- replace with your lead ids and date

-- For a range of ids:
-- UPDATE leads
-- SET created_at = '2026-10-15 00:00:00+00'::TIMESTAMPTZ
-- WHERE id BETWEEN 1 AND 50
--   AND deleted_at IS NULL;


-- ── STEP 4: Verify ────────────────────────────────────────────────────────────
-- After running the updates, check the result:
SELECT
    source_id,
    DATE(created_at) AS lead_date,
    COUNT(*)         AS count
FROM leads
WHERE deleted_at IS NULL
GROUP BY source_id, DATE(created_at)
ORDER BY lead_date DESC, source_id;
