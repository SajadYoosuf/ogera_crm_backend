-- Forgot password: reset codes go to the admin account's email, so that email is set to the
-- verified mailbox here. Only the active admin with id 1 is changed.
UPDATE employees
SET email = 'ogeracalicut@gmail.com'
WHERE id = 1 AND role = 'admin' AND is_active = 1 AND deleted_at IS NULL;

-- Created automatically on server start (Base.metadata.create_all); kept here for manual setup.
CREATE TABLE IF NOT EXISTS password_reset_codes (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  employee_id BIGINT NOT NULL,
  code_hash VARCHAR(64) NOT NULL,
  attempts SMALLINT NOT NULL DEFAULT 0,
  expires_at DATETIME NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  consumed_at DATETIME NULL,
  CONSTRAINT fk_password_reset_employee FOREIGN KEY (employee_id) REFERENCES employees(id)
);
