-- Dealer pipeline: add Aware stage after Meeting Done
-- Run once against the live database.
--
-- Changes:
--   1. Shift terms_agreed 4→5, sample_sent 5→6, first_order 6→7,
--      placed 7→8, active_dealer 8→9, inactive_dealer 9→10.
--   2. Insert 'aware' at order 4 for dealer.

-- Step 1 – shift existing stages up (highest order first to avoid unique-constraint collision)
UPDATE stage_types SET `order` = 10 WHERE slug = 'inactive_dealer' AND contact_type = 'dealer';
UPDATE stage_types SET `order` = 9  WHERE slug = 'active_dealer'   AND contact_type = 'dealer';
UPDATE stage_types SET `order` = 8  WHERE slug = 'placed'          AND contact_type = 'dealer';
UPDATE stage_types SET `order` = 7  WHERE slug = 'first_order'     AND contact_type = 'dealer';
UPDATE stage_types SET `order` = 6  WHERE slug = 'sample_sent'     AND contact_type = 'dealer';
UPDATE stage_types SET `order` = 5  WHERE slug = 'terms_agreed'    AND contact_type = 'dealer';

-- Step 2 – insert aware for dealer (idempotent)
INSERT INTO stage_types (slug, name, description, contact_type, `order`, is_active)
SELECT 'aware', 'Aware', 'Dealer understands the product range.', 'dealer', 4, 1
 WHERE NOT EXISTS (
     SELECT 1 FROM stage_types WHERE slug = 'aware' AND contact_type = 'dealer'
 );
