-- Add branch column to employees table.
-- Valid values: malappuram, perinthalmanna, kottakkal (enforced at application layer).
-- Existing rows keep NULL until their branch is set via the edit form.
ALTER TABLE employees
    ADD COLUMN branch VARCHAR(60) NULL AFTER phone;
