# Products catalogue reinstated (backend)

## Why
Rule 9 in `rule.md` had removed the products catalogue. The team decided to bring it back so tanks can be listed with specs and pricing.

## What changes
- `routes/products.py`: list gains a `material_id` filter. `GET /products/{id}` always returns `features` as a list, even for rows saved before features existed (their NULL used to cause a 500). The photo upload endpoint is removed; the photo is not part of the catalogue.
- `schemas/product.py`: `main_image_url` is optional on create (it was required). List rows include `tagline` and `special_price_active`. `features` coerces NULL to an empty list.
- `models/__init__.py`: registers `Product` and `ProductOption` so `create_all` creates their tables.
- `tests/test_products.py`: upload tests removed; archive/restore test and a NULL-features regression test added.
- `API.md` (root): new Products section.

## Out of scope
- Mobile products screen.
- Dropping the product tables or the old `main_image_url` column.
- Sales stay unlinked from products (rule 9).

## Verification
- `pytest tests/test_products.py tests/test_sales.py`: 15 passed.
- Full backend suite: 194 passed, 1 failed (`test_type_counts_respect_visibility`, which passes when run alone; order-dependent, not investigated further).
- Browser pass: create, edit, archive, restore, search, and a sales executive getting 403 on create (before the photo removal; re-check recommended).
