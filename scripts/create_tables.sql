-- Create tables in dependency order

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

INSERT IGNORE INTO roles (slug, name, description, is_system) VALUES
    ('admin', 'Admin', 'Full system access', 1),
    ('manager', 'Manager', 'Manages staff, all leads, reports', 1),
    ('team_lead', 'Team Lead', 'Supervises a team of callers / sales', 1),
    ('sales_executive', 'Sales Executive', 'Sales executive (closes deals)', 1),
    ('tele_caller', 'Tele Caller', 'Makes outbound/inbound calls, qualifies leads', 1),
    ('field_executive', 'Field Executive', 'On-site visits, measurements, installs', 1),
    ('support', 'Support', 'After-sales / customer support', 1);

CREATE TABLE IF NOT EXISTS employees (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    email VARCHAR(180) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(40) NOT NULL DEFAULT 'sales_executive',
    phone VARCHAR(20),
    is_active SMALLINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at DATETIME,
    FOREIGN KEY(role) REFERENCES roles(slug) ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS lead_sources (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(60) NOT NULL UNIQUE,
    slug VARCHAR(40) NOT NULL UNIQUE,
    is_active SMALLINT NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS stage_types (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(60) NOT NULL UNIQUE,
    slug VARCHAR(40) NOT NULL UNIQUE,
    description VARCHAR(255),
    `order` SMALLINT NOT NULL DEFAULT 0,
    is_active SMALLINT NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS contacts (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    contact_type ENUM('consumer', 'partner', 'dealer') NOT NULL DEFAULT 'consumer',
    name VARCHAR(150) NOT NULL,
    phone VARCHAR(20) NOT NULL,
    phone_alt VARCHAR(20),
    district VARCHAR(80) NOT NULL,
    address VARCHAR(255),
    latitude DECIMAL(9, 6),
    longitude DECIMAL(9, 6),
    assigned_to BIGINT NOT NULL,
    created_by BIGINT,
    referred_by_id BIGINT,
    lead_source_id INT,
    notes TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at DATETIME,
    FOREIGN KEY(assigned_to) REFERENCES employees(id),
    FOREIGN KEY(created_by) REFERENCES employees(id),
    FOREIGN KEY(referred_by_id) REFERENCES contacts(id),
    FOREIGN KEY(lead_source_id) REFERENCES lead_sources(id),
    INDEX idx_contacts_type (contact_type),
    INDEX idx_contacts_assigned (assigned_to),
    INDEX idx_contacts_district (district),
    INDEX idx_contacts_created_by (created_by)
);

CREATE TABLE IF NOT EXISTS lead_stages (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    contact_id BIGINT NOT NULL,
    stage ENUM('new_lead', 'first_call', 'aware', 'interested', 'quoted', 'won', 'lost', 'referral') NOT NULL,
    changed_by BIGINT NOT NULL,
    changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    loss_reason ENUM('price', 'competitor', 'timing', 'not_interested', 'other'),
    loss_notes TEXT,
    awareness_note TEXT,
    FOREIGN KEY(contact_id) REFERENCES contacts(id),
    FOREIGN KEY(changed_by) REFERENCES employees(id),
    INDEX idx_lead_stages_contact_id (contact_id),
    INDEX idx_lead_stages_stage (stage)
);

CREATE TABLE IF NOT EXISTS consumer_profiles (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    contact_id BIGINT NOT NULL UNIQUE,
    awareness_mode SMALLINT,
    house_stage VARCHAR(50),
    current_tank_type VARCHAR(50),
    current_tank_capacity INT,
    current_tank_age INT,
    FOREIGN KEY(contact_id) REFERENCES contacts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS partner_profiles (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    contact_id BIGINT NOT NULL UNIQUE,
    awareness_mode SMALLINT,
    profession_type VARCHAR(80) NOT NULL,
    work_area VARCHAR(120),
    sample_given SMALLINT,
    projects_per_year SMALLINT,
    FOREIGN KEY(contact_id) REFERENCES contacts(id) ON DELETE CASCADE
);
