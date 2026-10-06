-- lead_activities v2: type / scheduled_date / scheduled_time / assigned_to / completed_by.
-- The first version of the table was empty, so it is dropped and recreated.
DROP TABLE IF EXISTS lead_activities;

CREATE TABLE lead_activities (
    id BIGINT NOT NULL AUTO_INCREMENT,
    contact_id BIGINT NOT NULL,
    type VARCHAR(20) NOT NULL,
    notes TEXT NULL,
    status VARCHAR(10) NOT NULL,
    scheduled_date DATE NULL,
    scheduled_time TIME NULL,
    assigned_to BIGINT NULL,
    completed_at DATETIME NULL,
    completed_by BIGINT NULL,
    latitude DECIMAL(9, 6) NULL,
    longitude DECIMAL(9, 6) NULL,
    created_by BIGINT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_lead_activities_contact_id (contact_id),
    KEY idx_lead_activities_due (status, scheduled_date),
    KEY idx_lead_activities_assigned (assigned_to),
    FOREIGN KEY (contact_id) REFERENCES contacts (id),
    FOREIGN KEY (assigned_to) REFERENCES employees (id),
    FOREIGN KEY (completed_by) REFERENCES employees (id),
    FOREIGN KEY (created_by) REFERENCES employees (id)
);
