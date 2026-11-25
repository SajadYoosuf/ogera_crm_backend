-- Settings access per module: Team, Integrations, Lead statuses, Company.
-- Admin gets everything; manager gets Integrations only.
-- Run once per tenant schema (the roles table lives in each tenant schema).
-- Merges into the existing permissions JSON, so other keys are kept.

UPDATE roles
SET permissions = permissions || '{"team":"crud","integrations":"crud","lead_statuses":"crud","company":"crud"}'::jsonb
WHERE slug = 'admin';

UPDATE roles
SET permissions = permissions || '{"team":"","integrations":"ru","lead_statuses":"","company":""}'::jsonb
WHERE slug = 'manager';
