# Link sales to the products catalogue

## Why
`sales.purchased_product` is free text, so the same tank is spelt many ways and a sale cannot be tied to a catalogue product. Sales should use the product data structure (`products`).

## What changes
- `sales.product_id` (nullable FK to `products.id`, indexed): `scripts/migrate_sales_product_id.sql` (manual, with an optional backfill by exact name).
- `POST/PUT /sales` accept `product_id`. The product must exist and not be deleted (400 otherwise). The server copies the product name into `purchased_product`, so old rows, the product filter and the list keep working. Changing the `purchased_product` text without `product_id` unlinks the sale.
- `GET /sales` accepts `product_id`; `SaleResponse` returns `product_id`.
- `API.md` updated.

## Out of scope
Price or quantity on a sale, stock, linking sales to contacts.

## Verification
pytest `tests/test_sales_products.py` (SQLite, same style as `tests/test_products.py`).
