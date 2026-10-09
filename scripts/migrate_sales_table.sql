-- Past sales from the buyers register. Not linked to contacts.
CREATE TABLE IF NOT EXISTS sales (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    purchase_date DATE NULL,
    name VARCHAR(150) NOT NULL,
    phone VARCHAR(20) NULL,
    alt_phone VARCHAR(20) NULL,
    address TEXT NULL,
    purchased_product VARCHAR(150) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX ix_sales_purchase_date (purchase_date),
    INDEX ix_sales_phone (phone)
);

-- For a sales table created before purchased_product existed:
-- ALTER TABLE sales ADD COLUMN purchased_product VARCHAR(150) NULL AFTER address;
