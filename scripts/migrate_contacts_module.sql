-- Contacts module: consumer, partner and dealer in one API, fixed lists in code, awareness mode removed.
-- Run once on an existing database, in order, after taking a backup. The dealer_profiles table is created by the
-- app on startup (create_all), so it is not here. Each step says what it changes.

-- 1. Phone key: last 10 digits, used by the duplicate check across all contact types.
ALTER TABLE contacts ADD COLUMN phone_key VARCHAR(10) NULL;
UPDATE contacts SET phone_key = RIGHT(REGEXP_REPLACE(phone, '[^0-9]', ''), 10);
ALTER TABLE contacts MODIFY phone_key VARCHAR(10) NOT NULL;
CREATE INDEX idx_contacts_phone_key ON contacts (phone_key);

-- 2. District is optional (the web form may leave it blank; the mobile app requires it).
ALTER TABLE contacts MODIFY district VARCHAR(80) NULL;
-- Old imports stored the placeholder "Unknown" for a missing district. It is cleared so it no longer counts as filled.
UPDATE contacts SET district = NULL WHERE district = 'Unknown';

-- 3. House stage: map the old free text to the fixed list (ready, under_construction, renovation).
UPDATE consumer_profiles SET house_stage = 'under_construction' WHERE LOWER(house_stage) LIKE 'under construction%';
UPDATE consumer_profiles SET house_stage = 'renovation'         WHERE LOWER(house_stage) LIKE 'renovat%';
UPDATE consumer_profiles SET house_stage = 'ready'              WHERE LOWER(house_stage) LIKE 'ready%';
-- Anything left over is reported below and needs a manual decision; it is not guessed.
SELECT house_stage, COUNT(*) AS rows_left FROM consumer_profiles
WHERE house_stage IS NOT NULL AND house_stage NOT IN ('ready', 'under_construction', 'renovation')
GROUP BY house_stage;

-- 4. Tank type: fixed list (plastic, concrete, steel, other). Old "Pvc" becomes plastic.
UPDATE consumer_profiles SET current_tank_type = 'plastic'  WHERE LOWER(current_tank_type) IN ('pvc', 'plastic');
UPDATE consumer_profiles SET current_tank_type = 'concrete' WHERE LOWER(current_tank_type) = 'concrete';
UPDATE consumer_profiles SET current_tank_type = 'steel'    WHERE LOWER(current_tank_type) = 'steel';
UPDATE consumer_profiles SET current_tank_type = 'other'
WHERE current_tank_type IS NOT NULL AND current_tank_type NOT IN ('plastic', 'concrete', 'steel', 'other');

-- 5. Partner profession: lower-case with underscores; anything not in the fixed list becomes other.
UPDATE partner_profiles SET profession_type = LOWER(REPLACE(TRIM(profession_type), ' ', '_'));
UPDATE partner_profiles SET profession_type = 'other'
WHERE profession_type NOT IN ('plumber', 'engineer', 'contractor', 'interior_designer', 'architect', 'electrician', 'other');

-- 6. Duplicate phones are reported, not blocked. Fix them by hand (the app blocks new duplicates from now on).
SELECT phone_key, COUNT(*) AS contacts_sharing_number
FROM contacts WHERE deleted_at IS NULL
GROUP BY phone_key HAVING COUNT(*) > 1;

-- 7. Awareness mode is replaced by the Aware stage in the lead pipeline. Check the Aware stage is in use before dropping:
--    SELECT COUNT(*) FROM lead_stages WHERE stage = 'aware';
-- Only then remove the columns (irreversible):
-- ALTER TABLE consumer_profiles DROP COLUMN awareness_mode;
-- ALTER TABLE partner_profiles DROP COLUMN awareness_mode;

-- 8. Settings: the partner type list is no longer in Settings. Its rows stay in enum_values but are not used.
--    To remove them: DELETE FROM enum_values WHERE category = 'professional_type';
