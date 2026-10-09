# Sales table (buyers register)

## Why
`buyers_register.xlsx` is past sales data. It is not contact data, so it gets its own table instead of being inserted into `contacts`.

## What changes
- `models/sale.py` (new): `Sale` -> table `sales` (purchase_date, name, phone, alt_phone, address, purchased_product, created_at). No foreign key to `contacts`; phone is nullable because some rows have none.
- `models/__init__.py`: exports `Sale`, so `create_all` in `main.py` creates the table.
- `scripts/migrate_sales_table.sql`: same table as plain SQL.
- `scripts/import_sales.py` (new): loads the buyers register; refuses to run on a non-empty table.

## Out of scope
Linking sales to contacts, API routes and screens for sales, web and mobile.

## Verification
Table created on the dev database, 112 rows loaded, count and sample rows checked.

## Endpoints (added)
- `schemas/sale.py`, `routes/sales.py` (registered in `routes/__init__.py` and `main.py`): `GET /sales` (search, product, date range, paging), `GET /sales/products`, `GET/POST/PUT/DELETE /sales/{id}`. Read: any signed-in user; write: admin and manager. Phones are normalised with `contact_import.normalize_phone`. Documented in `API.md`.
- `purchased_product` stays free text for now; it can be tied to the products catalogue later.
- Verified with a TestClient on the dev database (list, search, date filter, create, blank-to-null update, bad phone 422, delete, sales role read 200 / write 403). The whole app could not be imported at that moment because the separate products module was mid-edit.
