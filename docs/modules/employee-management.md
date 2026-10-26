# Module: Employee management

Covers the staff accounts: who can see them, how they are created, changed, deactivated and removed, and how their data links to the rest of the CRM.

Status: committed (`routes/employees.py`, `schemas/employee.py`, `models/employee.py`, `mailer.py`). The role catalogue is a separate module (`/roles`).

## Files

| File | What it does |
| --- | --- |
| `routes/employees.py` | All `/employees` routes and the rules behind them |
| `schemas/employee.py` | Request and response shapes; phone, password and email checks |
| `models/employee.py` | `Employee` table |
| `models/role.py` | `Role` table and the built-in role list (`SYSTEM_ROLES`) |
| `mailer.py` | `send_employee_credentials` (new account), `send_password_changed_notice` (password change) |
| `config.py` | `PASSWORD_MIN_LENGTH`, `PASSWORD_MAX_LENGTH` |

## Roles

`employees.role` holds a role slug from the `roles` table. The built-in slugs are `admin`, `manager`, `team_lead`, `sales_executive` (the default), `tele_caller`, `field_executive` and `support`. Admins can add custom roles. A role used by an employee cannot be deleted; it is deactivated instead.

Two roles have extra rules across the module:

- `admin` and `manager` are **monitoring** roles. They see everything and never receive work, so they are never offered as assignees (see rule 1 in `rule.md`).
- Only an `admin` can create, promote, change or delete an `admin` or `manager` account.

## Routes

| Method and path | Who may call | Result |
| --- | --- | --- |
| `GET /employees/directory` | Any signed-in employee | Active staff as `id`, `name`, `role`, `phone`, sorted by name |
| `GET /employees` | Any signed-in employee | Paged list with filters |
| `GET /employees/{id}` | Any signed-in employee | One employee |
| `POST /employees` | `admin`, `manager` | Creates an employee and emails the login details |
| `PUT /employees/{id}` | `admin`, `manager` | Partial update; a password change sends a notice email |
| `DELETE /employees/{id}` | `admin`, `manager` | Soft delete |

## Who sees which employees

- **Hidden roles.** `admin` accounts are never returned by the list or by id, to anyone. A `manager` caller also cannot see other `manager` accounts. A hidden id gives `404`, the same as an unknown id.
- **The directory is not filtered by hidden roles.** It lists every active, non-deleted employee, including admins and managers, with only `id`, `name`, `role` and `phone`.
- **Deleted employees** (`deleted_at` set) are excluded from lists and from the directory.
- **Inactive employees** (`is_active = 0`) are excluded from the directory, so they are not offered as assignees. They still appear in the employee list, which shows `is_active` as the account status.

## Business logic

### Create (`POST /employees`)

1. Only an admin may assign the `admin` or `manager` role (`403`).
2. The role must exist and be active (`422`).
3. The email must be unused, ignoring case. A soft-deleted employee still holds their email (`409`).
4. If a phone is given, it must not match another non-deleted employee. Phones are compared on their last 10 digits, so `+91 90000 00003` and `9000000003` match (`409`).
5. The password is hashed with argon2 and stored. The plain password is never saved or returned.
6. The row is inserted. A database uniqueness conflict is also reported as `409`.
7. The login details are emailed before the transaction is committed. If the email fails, the transaction is rolled back and the employee is not created; the API returns `503` so the request can be retried.

### Update (`PUT /employees/{id}`)

1. The employee must exist and not be deleted (`404`).
2. Required fields (`name`, `email`, `role`, `is_active`, `password`) cannot be `null` (`422`).
3. Only an admin may change an `admin` or `manager` account, unless it is the caller's own (`403`).
4. A role change is checked as in create: only an admin may grant `admin` or `manager`, and the role must be active.
5. Nobody can deactivate their own account or change their own role (`400`).
6. A new email or phone must be unused, by the same rules as create (`409`).
7. A new password is hashed. The employee is then emailed a notice that the password changed. A failed email does not undo the change.

### Delete (`DELETE /employees/{id}`)

1. Nobody can delete their own account (`400`).
2. Only an admin may delete an `admin` or `manager` (`403`).
3. The row is kept. It gets `deleted_at` set and `is_active` set to 0. The employee can no longer sign in, and the email stays reserved.

Delete is soft because the employee is referenced by contacts, activities, stage history, locations and reset codes. A hard delete would break those links.

### Validation (`schemas/employee.py`)

| Field | Rule |
| --- | --- |
| `name` | 1–120 characters, trimmed, not blank |
| `email` | Valid email, stored lowercase |
| `phone` | Spaces, `-` and `()` removed; then 7–15 digits with an optional leading `+` |
| `role` | Lowercase slug, `^[a-z][a-z0-9_]{1,39}$`, then checked against `roles` |
| `password` | `PASSWORD_MIN_LENGTH` to `PASSWORD_MAX_LENGTH` characters (6–15 by default); at least one letter and one digit; at most 72 bytes |

## Database

```
employees (one row per staff account)
---------
id              PK
name            VARCHAR(120)
email           VARCHAR(180) UNIQUE          lowercase
password_hash   VARCHAR(255)                 argon2
role            VARCHAR(40) FK roles.slug    ON UPDATE CASCADE
phone           VARCHAR(20) NULL             no unique index (checked in code)
is_active       SMALLINT default 1           admin switch: may sign in
deleted_at      DATETIME NULL                soft delete
created_at, updated_at

roles
-----
id, slug UNIQUE, name UNIQUE, description, is_system, is_active

Rows that point at employees.id (kept on soft delete):
  contacts.assigned_to, contacts.created_by
  lead_activities.assigned_to, lead_activities.completed_by, lead_activities.created_by
  lead_stages.changed_by
  staff_locations.employee_id          ON DELETE CASCADE
  staff_location_states.employee_id    ON DELETE CASCADE
  password_reset_codes.employee_id
```

`is_field_staff` was removed from the model. If the column still exists in an older database, drop it with `ALTER TABLE employees DROP COLUMN is_field_staff;`. The code no longer reads or writes it.

## End-to-end flow

```
Admin creates a tele caller
  web/mobile ──POST /employees {name,email,phone,role,password}──▶ routes/employees.py
    ▶ schemas/employee.py: field rules (phone, password length, role slug)
    ▶ roles: role exists and is active
    ▶ employees: email unused (any case) and phone unused (last 10 digits)
    ▶ argon2 hash ▶ INSERT employees
    ▶ mailer.send_employee_credentials ──▶ new employee's inbox
    ◀── 201 {employee, credentials_email} (or 503 when email sending fails; no employee is saved)

Staff pick an assignee
  app ──GET /employees/directory──▶ employees where deleted_at IS NULL AND is_active = 1
    ◀── [{id,name,role,phone}]   (includes admin and manager accounts; the web
                                  app leaves them out of assignee pickers, and the
                                  backend refuses to assign a follow-up to them)

Admin resets a password
  app ──PUT /employees/{id} {password}──▶ checks (hidden roles, self, phone/email)
    ▶ argon2 hash ▶ UPDATE employees.password_hash
    ▶ mailer.send_password_changed_notice ──▶ employee's inbox
    ◀── 200 EmployeeResponse

Admin removes an employee
  app ──DELETE /employees/{id}──▶ checks (self, admin-only roles)
    ▶ UPDATE employees SET deleted_at = now, is_active = 0
    ◀── 204
```

## Settings

| Name | Default | Used for |
| --- | --- | --- |
| `PASSWORD_MIN_LENGTH` | 6 | Shortest password (create, update and reset) |
| `PASSWORD_MAX_LENGTH` | 15 | Longest password (create, update and reset) |
| `SMTP_*`, `APP_LOGIN_URL` | — | Credentials and notice emails; email is skipped with a log line when `SMTP_HOST` is unset |

## Known gaps

1. **Employee reads expose contact details** of every non-hidden employee to every signed-in employee. Intended for the directory, but the full list also shows email and phone.
2. **Phone uniqueness is enforced in code only.** There is no unique index, so two creates at the same moment could store the same number. A unique index needs a clean-up of existing duplicates first.
3. **`password_hash` truncation comment.** `get_password_hash` cuts to 72 characters, and the comment says bytes. Passwords are limited to 15 characters, so this has no effect today.
4. **Deactivated accounts** keep working on sign-in-only routes until their token expires (see the authentication module).

## Tests

`tests/test_employee_duplicates.py` (email and phone duplicates, country-code phones, soft-deleted rows), `tests/test_employee_passwords.py` (length limits, change notice), `tests/test_mobile_api.py` (directory, list visibility, hidden admins and managers, deactivation).
