# Admin employee edit moves the login lookup when an email changes

## What changed
- `routes/employees.py`, `PUT /employees/{id}`: when `email` changes, the public login row `public.employee_tenant_map` (the `EmployeeLookup` model) is moved to the new address in the same request. If no row existed for the employee, one is added.
- The new address must not be used in this tenant or in `public.employee_tenant_map` (409, same message as before).
- Emails are stored lowercase in both the tenant `employees` table and the lookup, because login matches lowercase.
- `update_employee` now uses the tenant session (`get_tenant_db`) and a public session (`get_public_db`) instead of `get_db`. `get_db` does not set the tenant schema, so this route could read or write the wrong schema.
- Both sessions roll back if either save fails.
- `tests/test_employee_email_lookup.py`: a changed email logs in, the old one stops working, and an address in use returns 409.

## Access / data impact
- Same access as before: admin and manager only.
- Writes to `public.employee_tenant_map` and the tenant `employees` row. No schema change.
- Correction to `2026-11-26-my-account-update.md`: the table is `public.employee_tenant_map`, not `employee_lookup`.

## Verification
- Run: `tests/test_employee_email_lookup.py`, 2 passed on SQLite.
- Run: `tests/test_employees.py`, `test_employee_security_fixes.py`, `test_employee_passwords.py`, `test_employee_duplicates.py`, `test_auth.py`, `test_settings.py`. 11 failures, all already on clean HEAD. The rest pass.
- Not run: PostgreSQL, or the web app's employee edit form.

## Known issues or not done yet
- `POST /employees` (create) does not add a `public.employee_tenant_map` row, so a new employee may not be able to log in. Not checked yet; needs a look.
- The other `routes/employees.py` routes still use `get_db`. Only `update_employee` was switched.
- The `employees` routes that delete or deactivate an employee do not touch the lookup row. Login still checks `is_active` in the tenant, so this is not a security hole, but the row is left behind.
