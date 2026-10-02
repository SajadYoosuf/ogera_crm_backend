-- "Field Visit" and "Offline" are the same lead source. Keep "Offline" (the slug the GPS rule in
-- routes/contacts.py checks) and move everything from "Field Visit" onto it. Safe to run more than once.

-- If only Field Visit exists, just rename it.
UPDATE lead_sources
SET name = 'Offline', slug = 'offline'
WHERE slug = 'field_visit'
  AND NOT EXISTS (SELECT 1 FROM (SELECT id FROM lead_sources WHERE slug = 'offline') AS existing);

-- If both exist, repoint contacts to Offline, then drop Field Visit.
UPDATE contacts
SET lead_source_id = (SELECT id FROM (SELECT id FROM lead_sources WHERE slug = 'offline') AS target)
WHERE lead_source_id IN (SELECT id FROM (SELECT id FROM lead_sources WHERE slug = 'field_visit') AS old);

DELETE FROM lead_sources WHERE slug = 'field_visit';
