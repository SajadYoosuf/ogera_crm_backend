-- Move employee roles from a fixed ENUM to a managed `roles` table.
-- Run after migrate_employee_roles.sql. Existing employees keep their current role slug.
-- Step 1: roles table.
CREATE TABLE IF NOT EXISTS roles (
    id INT AUTO_INCREMENT PRIMARY KEY,
    slug VARCHAR(40) NOT NULL UNIQUE,
    name VARCHAR(80) NOT NULL UNIQUE,
    description VARCHAR(255),
    is_system SMALLINT NOT NULL DEFAULT 0,
    is_active SMALLINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- Step 2: built-in roles.
INSERT IGNORE INTO roles (slug, name, description, is_system) VALUES
    ('admin', 'Admin', 'Full system access', 1),
    ('manager', 'Manager', 'Manages staff, all leads, reports', 1),
    ('team_lead', 'Team Lead', 'Supervises a team of callers / sales', 1),
    ('sales_executive', 'Sales Executive', 'Sales executive (closes deals)', 1),
    ('tele_caller', 'Tele Caller', 'Makes outbound/inbound calls, qualifies leads', 1),
    ('field_executive', 'Field Executive', 'On-site visits, measurements, installs', 1),
    ('support', 'Support', 'After-sales / customer support', 1);

-- Step 3: employees.role becomes free text that must match a role.
ALTER TABLE employees
    MODIFY COLUMN role VARCHAR(40) NOT NULL DEFAULT 'sales_executive';

ALTER TABLE employees
    ADD CONSTRAINT fk_employees_role FOREIGN KEY (role) REFERENCES roles(slug) ON UPDATE CASCADE;
