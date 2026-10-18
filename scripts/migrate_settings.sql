-- Settings page: editable dropdown options and company profile.
-- Run once on an existing database. New tables (enum_values, company_settings) are created by the app on startup
-- and seeded with the options the app already used, so only the changes to existing tables are here.

-- Loss reasons are now managed in Settings, so lead_stages.loss_reason stops being a fixed ENUM.
-- Existing values (price, competitor, timing, not_interested, other) are kept as they are.
ALTER TABLE lead_stages MODIFY COLUMN loss_reason VARCHAR(40) NULL;
