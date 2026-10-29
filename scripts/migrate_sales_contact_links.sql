-- Link sales to contacts and record the authenticated employee who created them.
-- Run once on an existing MySQL database before deploying the new backend.

ALTER TABLE sales
    ADD COLUMN contact_id BIGINT NULL,
    ADD COLUMN created_by_employee_id BIGINT NULL,
    ADD INDEX idx_sales_contact_id (contact_id),
    ADD INDEX idx_sales_created_by_employee_id (created_by_employee_id),
    ADD CONSTRAINT fk_sales_contact
        FOREIGN KEY (contact_id) REFERENCES contacts (id),
    ADD CONSTRAINT fk_sales_created_by_employee
        FOREIGN KEY (created_by_employee_id) REFERENCES employees (id);

-- Backfill historical rows only when exactly one live contact matches the
-- normalized last ten digits of the sale phone. Ambiguous and unmatched rows
-- remain unlinked for manual review.
UPDATE sales AS s
JOIN contacts AS c
  ON c.deleted_at IS NULL
 AND c.phone_key = RIGHT(REGEXP_REPLACE(COALESCE(s.phone, ''), '[^0-9]', ''), 10)
SET s.contact_id = c.id,
    s.created_by_employee_id = COALESCE(c.created_by, s.created_by_employee_id)
WHERE s.contact_id IS NULL
  AND (
      SELECT COUNT(*)
      FROM contacts AS c2
      WHERE c2.deleted_at IS NULL
        AND c2.phone_key = RIGHT(REGEXP_REPLACE(COALESCE(s.phone, ''), '[^0-9]', ''), 10)
  ) = 1;

-- Review rows that could not be linked automatically:
-- SELECT s.id, s.name, s.phone
-- FROM sales AS s
-- WHERE s.contact_id IS NULL;
