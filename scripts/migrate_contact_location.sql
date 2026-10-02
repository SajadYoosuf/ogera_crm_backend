-- GPS location for offline (field visit) leads
ALTER TABLE contacts
    ADD COLUMN latitude DECIMAL(9, 6) NULL AFTER address,
    ADD COLUMN longitude DECIMAL(9, 6) NULL AFTER latitude;
