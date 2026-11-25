-- FCM device registrations (one row per browser / mobile token per employee).
-- Matches models/fcm_device.py. Without this table, every notification push logs
-- "relation fcm_devices does not exist" and no push is sent.
--
-- Run once PER TENANT SCHEMA, e.g.:
--   SET search_path TO ventino;
--   \i scripts/migrations/005_fcm_devices.sql
-- Idempotent (IF NOT EXISTS). Do NOT run against the live shared database without a backup.

CREATE TABLE IF NOT EXISTS fcm_devices (
    id           SERIAL PRIMARY KEY,
    employee_id  BIGINT NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    token        VARCHAR(512) NOT NULL,
    platform     VARCHAR(20),
    device_id    VARCHAR(255),
    last_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fcm_devices_token UNIQUE (token)
);

CREATE INDEX IF NOT EXISTS ix_fcm_devices_employee_id ON fcm_devices (employee_id);
CREATE INDEX IF NOT EXISTS ix_fcm_devices_last_seen_at ON fcm_devices (last_seen_at);

-- Optional one-time backfill for employees still using the legacy single-token column
-- (dispatch_notification_push also reads employees.fcm_token, so this is not required):
-- INSERT INTO fcm_devices (employee_id, token)
-- SELECT id, fcm_token FROM employees
-- WHERE fcm_token IS NOT NULL AND fcm_token <> ''
-- ON CONFLICT (token) DO NOTHING;
