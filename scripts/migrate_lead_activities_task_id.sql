-- Outcome log entries point at the follow-up task they record, and the staff task list gets its index.
ALTER TABLE lead_activities
    ADD COLUMN task_id BIGINT NULL AFTER longitude,
    ADD KEY idx_lead_activities_task_id (task_id),
    ADD CONSTRAINT fk_lead_activities_task FOREIGN KEY (task_id) REFERENCES lead_activities (id);

ALTER TABLE lead_activities
    DROP INDEX idx_lead_activities_assigned,
    ADD KEY idx_lead_activities_assigned (assigned_to, status, scheduled_date);
