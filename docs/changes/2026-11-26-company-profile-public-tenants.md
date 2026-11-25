# Company profile stored on public.tenants; settings routes use the tenant session

## What changed
- `routes/settings.py`, company endpoints (`GET /settings/company`, `PATCH /settings/company`, `POST /settings/company/logo`): the company name and logo are now read and written on the company's row in `public.tenants` (`name`, `logo_url`). The row is found by `schema_name` from the signed-in user's tenant (`employee._tenant_schema`). Before, they were stored in `company_settings` inside each tenant schema.
- `routes/settings.py`, enum endpoints (`/settings/enums...`): switched from `get_db` to `get_tenant_db`. `get_db` does not set the tenant `search_path`, so these routes could read the wrong schema.
- Root cause of the 500 on `GET /settings/company`: `get_db` ran the query against `public`, where `company_settings` does not exist. `database.py` runs `SET search_path` on pooled connections and never resets it, so `get_db` only worked when it happened to receive a connection left on a tenant schema.

## Access / data impact
- Who can read and change the company profile is unchanged: managers and admins read; the PATCH and logo routes keep the same role gate as before.
- Company name and logo are now shared with `public.tenants`, which holds one row per tenant. No new columns are added: `name` and `logo_url` already exist in the `Tenant` model.
- Existing values in any tenant's `company_settings` table are not copied to `public.tenants`. Any company name set there before this change will not show up.

## Verification
- Run: `tests/test_settings.py` and `tests/test_no_role_rules.py` on SQLite (`DATABASE_URL=sqlite:///:memory:`). The company tests pass (`test_company_read_is_manager_and_admin_but_not_staff`, `test_staff_cannot_read_or_write_company`, and the company checks in `test_no_role_rules.py`). 18 pass, 8 fail.
- The same 8 tests fail on a clean checkout of HEAD, so they predate this change. They are: `test_lead_stage_can_be_added_to_one_pipeline`, `test_change_my_name`, `test_changing_my_email_needs_the_current_password_and_a_free_address`, `test_settings_endpoints_require_a_token`, `test_sales_executive_cannot_sign_in_on_the_website`, `test_any_employee_sees_and_edits_every_contact`, `test_any_employee_can_add_a_sale_and_a_product`, `test_follow_up_can_be_assigned_to_a_manager`.
- Not run: PostgreSQL. The SQLite suite skips the `SET search_path` branch, so the tenant-schema path is not covered by tests.
- Not run: any query against the live database. Not confirmed: that the live `public.tenants` table has `name` and `logo_url` columns.

## Known issues or not done yet
- `settings_catalog.py`: `get_company`, `set_company`, and the `company_settings` seeding in `seed_defaults` are now unused. They should be removed in a follow-up along with the `CompanySetting` model.
- Add a `RESET search_path` when a tenant connection is returned to the pool in `database.py`, so `get_db()` cannot pick up a leftover tenant schema.
- `docs/changes/2026-10-09-settings.md` says the app creates `company_settings` and `enum_values` on startup. It does not, and the `company_settings` part is now stale.
- The 8 failing tests above need their own investigation.
