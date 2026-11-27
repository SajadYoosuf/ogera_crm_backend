# 2026-11-27 — Five route files now use the tenant session

Web side of the same incident:
`ogera_crm_web/docs/changes/2026-11-27-notification-polling.md`.

## What changed

Production returned 500 `relation "roles" does not exist` (and the same for `branches`). These route files used
`get_db`, which never runs `SET search_path`, so PostgreSQL looked for tenant tables in `public`:

- `routes/employees.py`
- `routes/branches.py`
- `routes/contact_fields.py`
- `routes/recent_activity.py`
- `routes/staff_locations.py`

All of them now use `get_tenant_db`, the session `get_current_user` already holds.

`POST /employees` now also adds the new address to `public.employee_tenant_map`. Without that row the new
employee gets 401 "Invalid email or password" at login. The create path never wrote it; the 500 hid this. An
address already mapped to another company returns 409 and saves nothing.

Tests:

- `tests/test_tenant_session_routes.py` (new): no route may depend on `get_db`.
- `tests/test_employee_create_login.py` (new): a created employee can log in; another company's address is
  refused.
- `tests/test_contacts_module.py`: removes its dependency overrides after each test. They stayed on the shared
  app, so later files read this module's database.

## Access / data impact

- The endpoints in the five files can now read and write tenant data, including creating, editing and deleting
  employees, contact field definitions and staff locations. Role checks are unchanged.
- Creating an employee writes one row to `public.employee_tenant_map`.
- No schema change and no migration.
- Employees created before this change without a lookup row still cannot log in. Changing their email through
  `PUT /employees/{id}` adds the row. The live table was not checked.

## Verification

- Throwaway local PostgreSQL 18 with one tenant schema and NullPool, as on Vercel. Before: all 17 calls to the
  affected endpoints returned 500. After: reads and writes succeed, a created employee logs in, and no
  connection is left idle in transaction. The only 404s are expected: admin accounts are hidden from every
  viewer, and a deleted employee is gone.
- Full pytest suite on SQLite: before `354 failed, 210 passed, 2 xfailed`; after `157 failed, 410 passed,
  2 xfailed`. No test fails after the change that passed before. Most of the improvement comes from the
  override cleanup.
- `uv lock --check`, `import main` and `py_compile` pass; no conflict markers.
- Not run: against Vercel or the live database. `uv sync --frozen` was not run; dependencies did not change.

## Known issues / not done

- 157 tests still fail in the full suite. In the employee, team access and recent activity files the failures
  seen when each file runs alone match the previous code. `test_contacts_module.py` alone fails 33 of 41, also
  as before.
- `GET /employees/{id}` returns 404 for an admin fetching their own record (existing behaviour, now reachable).
- `get_db` is still defined in `database.py`, and `routes/leads.py` still imports it without using it.
- `GET /notifications/stream` is unchanged: its 600 s lifetime exceeds Vercel's 300 s limit, and it holds a
  database session while open. Web builds with the polling change no longer call it.
- Slow requests are not addressed here (function region `iad1`, one new database connection per request).
