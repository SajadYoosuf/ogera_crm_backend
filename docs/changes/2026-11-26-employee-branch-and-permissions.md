# Permission checks work; employee branch is saved and returned

## What changed
- `auth.py`, `get_current_user`: attaches the role's `permissions` JSON to the employee as `_permissions`. `services/permissions.can()` reads that attribute, but nothing set it before, so every `require_permission` route answered 403, admin included (for example WhatsApp config).
- `routes/employees.py`: create and update save `branch` as `branch_id`, looked up by the branch code (`malappuram`, `perinthalmanna`, `kottakkal`). An unknown or inactive branch returns 422. List, detail, create and update return `branch` as the code.
- `schemas/employee.py`: `EmployeeResponse` has a `branch` field.
- `routes/employees.py`, `update_employee`: when `email` changes, the public login row `public.employee_tenant_map` moves with it (see `2026-11-26-admin-employee-email-lookup.md`).
- `tests/seed_data.py`: test roles get the production permissions (admin: every module; manager: head role, `integrations` read and update, sees all records) and the three branches.

## Access / data impact
- Access changes: routes guarded by `require_permission` now allow the roles whose JSON grants the action. Admin has `integrations` only if the live row has it (migration `003_settings_module_permissions.sql` or the one-line update). Until that is applied on the live database, admin will still get 403 on WhatsApp config.
- Data: `employees.branch_id` is written for the first time. Employees saved before this change have no branch until edited.
- No schema change.

## Verification
- `tests/test_employee_branch.py`: branch saved, returned in list and detail, cleared with null, unknown branch 422.
- `tests/test_whatsapp_config.py`: all pass after the permission fix.
- `tests/test_employee_email_lookup.py`: pass.
- Full-suite comparison with clean HEAD: the touched test files show no new failures. The full suite was not re-run after the last change because the test run was blocked.
- Not run: PostgreSQL or the live API.

## Known issues or not done yet
- Live database: confirm each tenant's `roles.permissions` includes `integrations` for admin, and that `branches` has the three codes.
- Login and `/auth/me` don't return `branch`.
- `POST /employees` still doesn't add a `public.employee_tenant_map` row (not checked; see the email-lookup note).
- The enum and company routes still use the older admin-or-manager check instead of permission keys.
