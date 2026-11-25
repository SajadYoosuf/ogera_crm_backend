# My account: PATCH /auth/me updates name and email

## What changed
- `routes/auth.py`: new `PATCH /auth/me`. The signed-in user can change their name (trimmed, cannot be blank) and their email.
- An email change needs `current_password`, checked against the stored hash. A missing or wrong password returns 400.
- The new email must not be used by another employee in this tenant (`_email_taken` from `routes/employees.py`, the same check the employee update uses) or by any tenant in `public.employee_lookup`. Either returns 409.
- Login finds accounts through `public.employee_lookup`, so an email change also moves that row in the same request. If the tenant save fails, the lookup change is rolled back.
- Returns the updated employee.

## Access / data impact
- Any signed-in employee can change only their own name and email. No role or permission changes.
- Writes to `public.employee_lookup` (the email key) and the tenant `employees` row. No schema change.

## Verification
- Run: `tests/test_settings.py` account tests (`test_change_my_name`, `test_changing_my_email_needs_the_current_password_and_a_free_address`, `test_settings_endpoints_require_a_token`) and `test_change_my_password`. All 4 pass on SQLite. The rest of `test_settings.py` plus `test_no_role_rules.py`: 5 failures, all already on clean HEAD.
- Not run: PostgreSQL, or the live endpoint from the web app.

## Known issues or not done yet
- `PUT /employees/{id}` (admin edits an employee) still changes only the tenant `employees` email. It does not update `employee_lookup`, so that employee cannot log in with a changed address. Needs the same lookup update.
