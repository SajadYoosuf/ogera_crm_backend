-- =============================================================================
-- Migration 002: Add permission columns to roles table
-- Target schema: ventino (replace with your tenant slug if different)
-- Run once on the live Hostinger PostgreSQL database.
-- Safe to re-run: all statements use IF NOT EXISTS / ON CONFLICT DO NOTHING.
-- =============================================================================

BEGIN;

SET search_path TO ventino;

-- ── Add new columns ───────────────────────────────────────────────────────────

ALTER TABLE roles
  ADD COLUMN IF NOT EXISTS parent_id        INTEGER REFERENCES roles(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS is_system        SMALLINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS description      VARCHAR(255),
  ADD COLUMN IF NOT EXISTS see_all_records  BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS permissions      JSONB NOT NULL DEFAULT '{}';

-- ── Mark existing roles as system roles ──────────────────────────────────────

UPDATE roles SET is_system = 1
  WHERE slug IN ('admin', 'manager', 'sales_executive', 'tele_caller',
                 'team_lead', 'field_executive', 'support');

-- ── Set data visibility ───────────────────────────────────────────────────────

UPDATE roles SET see_all_records = TRUE
  WHERE slug IN ('admin', 'manager');

-- ── Set human-readable names and descriptions ─────────────────────────────────

UPDATE roles SET name = 'Admin',          description = 'Full system access'                              WHERE slug = 'admin';
UPDATE roles SET name = 'Manager',         description = 'Manages staff, all leads, reports'               WHERE slug = 'manager';
UPDATE roles SET name = 'Sales Executive', description = 'Sales executive (closes deals)'                  WHERE slug = 'sales_executive';
UPDATE roles SET name = 'Tele Caller',     description = 'Makes outbound/inbound calls, qualifies leads'   WHERE slug = 'tele_caller';

-- Seed any missing roles -------------------------------------------------------

INSERT INTO roles (slug, name, is_system, description) VALUES
  ('team_lead',       'Team Lead',       1, 'Team lead — oversees a sales team'),
  ('field_executive', 'Field Executive', 1, 'Field visits and on-site sales'),
  ('support',         'Support',         1, 'Customer support and after-sales')
ON CONFLICT (slug) DO NOTHING;

-- ── Seed permissions per role ─────────────────────────────────────────────────

UPDATE roles SET permissions = '{"leads":"crud","contacts":"crud","employees":"crud","settings":"crud"}'
  WHERE slug = 'admin';

UPDATE roles SET permissions = '{"leads":"crud","contacts":"crud","employees":"crud","settings":"r"}'
  WHERE slug = 'manager';

UPDATE roles SET permissions = '{"leads":"crud","contacts":"cru","employees":"r","settings":""}'
  WHERE slug = 'sales_executive';

UPDATE roles SET permissions = '{"leads":"cr","contacts":"cr","employees":"r","settings":""}'
  WHERE slug = 'tele_caller';

UPDATE roles SET permissions = '{"leads":"crud","contacts":"cru","employees":"r","settings":""}'
  WHERE slug = 'team_lead';

UPDATE roles SET permissions = '{"leads":"crud","contacts":"cru","employees":"r","settings":""}'
  WHERE slug = 'field_executive';

UPDATE roles SET permissions = '{"leads":"r","contacts":"r","employees":"r","settings":""}'
  WHERE slug = 'support';

COMMIT;
