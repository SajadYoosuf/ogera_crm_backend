-- Run once on existing MySQL databases. New installations receive this table via create_all().
CREATE TABLE IF NOT EXISTS fcm_devices (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    employee_id BIGINT NOT NULL,
    token VARCHAR(512) NOT NULL,
    platform VARCHAR(20) NULL,
    device_id VARCHAR(255) NULL,
    last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_fcm_devices_token (token),
    KEY ix_fcm_devices_employee_id (employee_id),
    KEY ix_fcm_devices_last_seen_at (last_seen_at),
    CONSTRAINT fk_fcm_devices_employee
        FOREIGN KEY (employee_id) REFERENCES employees (id) ON DELETE CASCADE
);

-- Optional one-time backfill for employees using the legacy single-token column:
-- INSERT IGNORE INTO fcm_devices (employee_id, token)
-- SELECT id, fcm_token FROM employees WHERE fcm_token IS NOT NULL AND fcm_token <> '';
