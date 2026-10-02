-- Extend employees.role with the full CRM role set. Legacy 'sales' becomes 'sales_executive'.
-- Step 1: accept both old and new values so existing rows stay valid.
ALTER TABLE employees
    MODIFY COLUMN role ENUM('admin', 'manager', 'team_lead', 'sales', 'sales_executive', 'tele_caller', 'field_executive', 'support')
    NOT NULL DEFAULT 'sales_executive';

-- Step 2: convert legacy rows.
UPDATE employees SET role = 'sales_executive' WHERE role = 'sales';

-- Step 3: drop the legacy value.
ALTER TABLE employees
    MODIFY COLUMN role ENUM('admin', 'manager', 'team_lead', 'sales_executive', 'tele_caller', 'field_executive', 'support')
    NOT NULL DEFAULT 'sales_executive';
