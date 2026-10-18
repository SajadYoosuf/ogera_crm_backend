-- Adds the team link: each employee may report to one team lead.
-- Run once on an existing database. create_all() does not add columns to existing tables.
-- MySQL: run against the CRM database.

ALTER TABLE employees
    ADD COLUMN team_lead_id BIGINT NULL,
    ADD INDEX idx_employees_team_lead_id (team_lead_id),
    ADD CONSTRAINT fk_employees_team_lead
        FOREIGN KEY (team_lead_id) REFERENCES employees (id) ON DELETE SET NULL;
