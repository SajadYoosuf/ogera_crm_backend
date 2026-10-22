# Deferred Product Fields

These fields were removed from the `products` table on 2026-10-23 to keep the schema simple while the CRM is in early use. They are documented here so they can be added back when actually needed.

---

## `brand_id` (FK → product_options)

**What it was:** A foreign key linking a product to its brand (e.g. "Ventino"). Stored in the shared `product_options` table with `kind = 'brand'`.

**Why removed:** Only one brand exists right now. Filtering by brand adds UI complexity without benefit at this stage.

**When to add back:** Once you have 3+ brands and need to filter the product list by brand.

**How to add back:**
```sql
ALTER TABLE products ADD COLUMN brand_id INT NULL;
ALTER TABLE products ADD CONSTRAINT fk_products_brand FOREIGN KEY (brand_id) REFERENCES product_options(id);
```
Then add `brand_id` back to the SQLAlchemy model, Pydantic schemas, route filter params, and the frontend product modal/list.

---

## `category_id` (FK → product_options)

**What it was:** A foreign key linking a product to its category (e.g. "Water Tank", "Overhead Tank"). Stored in `product_options` with `kind = 'category'`.

**Why removed:** Same reason as brand — no categories are being used yet, and the filter clutter is confusing.

**When to add back:** When you need to group products by type and filter by category on the product list page.

**How to add back:** Same pattern as `brand_id` above, using `kind = 'category'`.

---

## `currency` (String, default "INR")

**What it was:** A 3-character currency code stored per product so different products could have different currencies.

**Why removed:** All products are priced in INR. No multi-currency use case exists yet.

**When to add back:** If you start selling internationally or importing products priced in foreign currencies.

**How to add back:**
```sql
ALTER TABLE products ADD COLUMN currency CHAR(3) NOT NULL DEFAULT 'INR';
```

---

## `special_price_active` (SmallInt 0/1)

**What it was:** A toggle that controlled whether the `sale_price` was shown on the product (e.g. for a seasonal sale — you pre-load the discounted price but only show it on sale day).

**Why removed:** Adds confusion. A simpler rule works fine for now: if `sale_price` is set and less than `mrp`, it is always shown.

**When to add back:** When you need scheduled or manually activated sales (e.g. a Diwali promotion where you pre-configure prices but activate them on a specific date).

**How to add back:**
```sql
ALTER TABLE products ADD COLUMN special_price_active TINYINT(1) NOT NULL DEFAULT 0;
```
Then restore the field in the model, schemas, and the frontend "Special price active" toggle.

---

## `main_image_url` (String)

**What it was:** A URL pointing to the product's primary display image (stored in cloud storage; the DB only holds the link).

**Why removed:** No image upload flow exists yet, so storing a URL that is always `null` adds noise.

**When to add back:** Once the image upload feature is built (upload to cloud storage, save URL here).

**How to add back:**
```sql
ALTER TABLE products ADD COLUMN main_image_url VARCHAR(500) NULL;
```
Then restore in the model, schemas, and product form/list.
