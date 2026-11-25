-- Creates the dropdown-options table used by Settings (activity types, loss reasons, districts, partner types).
-- Run once per tenant schema. Replace <tenant_schema> with the tenant slug, e.g. ventino.
-- Safe to re-run. Company name and logo live in public.tenants, not here.

SET search_path TO <tenant_schema>;

CREATE TABLE IF NOT EXISTS enum_values (
    id SERIAL PRIMARY KEY,
    category VARCHAR(50) NOT NULL,
    value VARCHAR(100) NOT NULL,
    label VARCHAR(100) NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active SMALLINT NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT now(),
    CONSTRAINT uq_category_value UNIQUE (category, value)
);

CREATE INDEX IF NOT EXISTS idx_category ON enum_values (category);
