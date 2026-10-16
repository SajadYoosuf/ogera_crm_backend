-- Consumer pipeline: fix stage order and add Negotiation stage
-- Run once against the live database.
--
-- Changes:
--   1. Fix 'aware' order: was seeded at 2 (old); correct position is 4 (after Connected).
--   2. Insert 'negotiation' stage at position 7 (between Quotation and Won).
--   3. Push 'won' from order 7 → 8, 'lost' from 8 → 9, 'after_sales_referring' from 9 → 10.

-- Step 1 – fix aware order (idempotent: only moves it if it is still at the wrong position)
UPDATE stage_types
   SET `order` = 4
 WHERE slug = 'aware'
   AND contact_type = 'consumer'
   AND `order` != 4;

-- Step 2 – shift won, lost, after_sales_referring up by one to make room for negotiation
--          Update highest order first to avoid a unique-constraint collision if the column is unique.
UPDATE stage_types SET `order` = 10 WHERE slug = 'after_sales_referring' AND contact_type = 'consumer';
UPDATE stage_types SET `order` = 9  WHERE slug = 'lost'                  AND contact_type = 'consumer';
UPDATE stage_types SET `order` = 8  WHERE slug = 'won'                   AND contact_type = 'consumer';

-- Step 3 – insert negotiation (skip if it already exists)
INSERT INTO stage_types (slug, name, description, contact_type, `order`, is_active)
SELECT 'negotiation', 'Negotiation', 'Price and terms under discussion.', 'consumer', 7, 1
 WHERE NOT EXISTS (
     SELECT 1 FROM stage_types WHERE slug = 'negotiation' AND contact_type = 'consumer'
 );
