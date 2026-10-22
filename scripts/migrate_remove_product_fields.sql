-- Remove fields that were deferred from the products table.
-- Run this once against your database after deploying the updated backend code.
-- Safe to run: each column is dropped individually so a partial failure is easy to recover from.

ALTER TABLE products DROP COLUMN brand_id;
ALTER TABLE products DROP COLUMN category_id;
ALTER TABLE products DROP COLUMN currency;
ALTER TABLE products DROP COLUMN special_price_active;
ALTER TABLE products DROP COLUMN main_image_url;
