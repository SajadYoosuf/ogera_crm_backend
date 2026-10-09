-- Link a sale to a catalogue product. purchased_product stays as the readable name.
ALTER TABLE sales
    ADD COLUMN product_id INT NULL AFTER purchased_product,
    ADD KEY ix_sales_product_id (product_id),
    ADD CONSTRAINT fk_sales_product FOREIGN KEY (product_id) REFERENCES products (id);

-- Optional backfill: link old rows whose text matches exactly one active product name.
UPDATE sales s
JOIN (
    SELECT LOWER(name) AS lname, MIN(id) AS id
    FROM products
    WHERE deleted_at IS NULL
    GROUP BY LOWER(name)
    HAVING COUNT(*) = 1
) p ON p.lname = LOWER(s.purchased_product)
SET s.product_id = p.id
WHERE s.product_id IS NULL;
