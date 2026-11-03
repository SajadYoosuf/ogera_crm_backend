# Module: Employee management

Covers the staff accounts: who can see them, how they are created, changed, deactivated and removed, and how their data links to the rest of the CRM.

Status: committed (`routes/employees.py`, `schemas/employee.py`, `models/employee.py`, `mailer.py`). The role catalogue is a separate module (`/roles`).
Last updated: 2026-11-03 — SQLModel migration, role-visibility tightened, tests corrected.

## Files

| File | What it does |
| --- | --- |
| `routes/employees.py` | All `/employees` routes and the rules behind them |
| `schemas/employee.py` | Request and response shapes; phone, password and email checks |
| `models/employee.py` | `Employee` SQLModel table (`table=True`) |
| `models/role.py` | `Role` table and the built-in role list (`SYSTEM_ROLES`) |
| `database.py` | Engine, shared registry, `get_db()` session, `SessionLocal` backward-compat export |
| `mailer.py` | `send_employee_credentials` (new account), `send_password_changed_notice` (password change) |
| `config.py` | `PASSWORD_MIN_LENGTH`, `PASSWORD_MAX_LENGTH` |

## Roles

`employees.role` holds a role slug from the `roles` table. The active slugs in use are `admin`, `manager`, and `sales_executive` (the default). Custom roles can be added via `/roles`. A role used by an employee cannot be deleted; it is deactivated instead.

Note: `team_lead`, `tele_caller`, `field_executive`, and `support` slugs may exist in older data but are not actively assigned. `team_lead_id` column was dropped from the DB.

Two roles have extra rules across the module:

- `admin` and `manager` are **monitoring** roles. They see everything and never receive work, so they are never offered as assignees.
- Only an `admin` can create, promote, change or delete an `admin` or `manager` account.

## Routes

| Method and path | Who may call | Result |
| --- | --- | --- |
| `GET /employees/directory` | Any signed-in employee | Active staff as `id`, `name`, `role`, `phone`, sorted by name |
| `GET /employees` | `admin`, `manager` only (403 for sales_executive) | Paged list with filters |
| `GET /employees/{id}` | Any signed-in employee | One employee |
| `POST /employees` | `admin`, `manager` | Creates an employee and emails the login details |
| `PUT /employees/{id}` | `admin`, `manager` | Partial update; a password change sends a notice email |
| `DELETE /employees/{id}` | `admin`, `manager` | Soft delete |
| `POST /employees/me/fcm-token` | Any signed-in employee | Register a push notification device token |
| `DELETE /employees/me/fcm-token` | Any signed-in employee | Unregister a push notification device token |

## Who sees which employees

- **`GET /employees` is admin and manager only.** Sales executives get `403`. They have no UI to browse the staff list on web or mobile.
- **Hidden roles.** `admin` accounts are never returned by the list or by-id endpoints, to any viewer. A `manager` caller also cannot see other `manager` accounts. A hidden id gives `404`, the same as an unknown id.
- **Self-lookup exception.** A viewer can always look up their own record (`GET /employees/{id}`) and edit themselves (`PUT /employees/{id}`), even if their role is normally hidden from others. This allows a manager to update their own name or email.
- **The directory is not filtered by hidden roles.** It lists every active, non-deleted employee with only `id`, `name`, `role` and `phone`, sorted by name. Used for assignment dropdowns in the UI.
- **Deleted employees** (`deleted_at` set) are excluded from lists and from the directory.
- **Inactive employees** (`is_active = 0`) are excluded from the directory, so they are not offered as assignees. They still appear in the employee list with `is_active` shown.

## Business logic

### Create (`POST /employees`)

1. Only an admin may assign the `admin` or `manager` role (`403`).
2. The role must exist and be active (`422`).
3. The email must be unused, ignoring case. A soft-deleted employee still holds their email (`409`).
4. If a phone is given, it must not match another non-deleted employee. Phones are compared on their last 10 digits, so `+91 90000 00003` and `9000000003` match (`409`).
5. The password is hashed with argon2 and stored. The plain password is never saved or returned.
6. The row is inserted. A database uniqueness conflict is also reported as `409`.
7. The employee row is inserted and committed first. The credentials email is then queued as a **background task** (sent after the response). A mailer failure does not roll back the employee. The response always returns `201` with `credentials_email.sent = false` (queued, not yet confirmed).

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
role            VARCHAR(40) FK roles.slug
phone           VARCHAR(20) NULL             no unique index (checked in code)
branch          VARCHAR(60) NULL             one of: malappuram, perinthalmanna, kottakkal
fcm_token       VARCHAR(512) NULL            legacy push token; new devices use fcm_devices table
is_active       SMALLINT default 1           admin switch: may sign in
deleted_at      DATETIME NULL                soft delete
created_at      DATETIME server_default      set by MySQL on INSERT
updated_at      DATETIME server_default      updated by MySQL on every change

roles
-----
id, slug UNIQUE, name UNIQUE, description, is_system, is_active

Rows that point at employees.id (kept on soft delete):
  contacts.assigned_to, contacts.created_by
  lead_stages.changed_by
  fcm_devices.employee_id              ON DELETE CASCADE
  attendance.employee_id               ON DELETE CASCADE
  expenses.employee_id, expenses.reviewed_by
  followup_schedules.assigned_to, followup_schedules.created_by
  followup_edit_log.changed_by
  followup_reschedule.changed_by
  contact_assignments.assigned_from, .assigned_to, .changed_by
  activity_log.done_by
  sales.created_by_employee_id
  password_reset.employee_id
  staff_locations.employee_id          ON DELETE CASCADE
  staff_location_history.employee_id   ON DELETE CASCADE
```

Columns dropped from the DB:
- `team_lead_id` — dropped; the team lead role is not in use.
- `is_field_staff` — no longer in the model or code; drop with `ALTER TABLE employees DROP COLUMN is_field_staff;` when ready.

## End-to-end flow

```
Admin creates a sales executive
  web/mobile ──POST /employees {name,email,phone,role,password}──▶ routes/employees.py
    ▶ schemas/employee.py: field rules (phone, password length, role slug)
    ▶ roles: role exists and is active
    ▶ employees: email unused (any case) and phone unused (last 10 digits)
    ▶ argon2 hash ▶ INSERT employees ▶ db.commit()
    ▶ background task: mailer.send_employee_credentials ──▶ new employee's inbox
    ◀── 201 {employee, credentials_email: {sent: false, message: "queued"}}

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

| File | What it covers |
| --- | --- |
| `tests/test_employees.py` | Full CRUD: create, list, get, update, delete, duplicates, self-guards |
| `tests/test_fcm.py` | FCM token registration, unregistration, push to all registered devices |
| `tests/test_no_role_rules.py` | Role-based access: who can see what, 403 for sales executive on list |
| `tests/test_employee_duplicates.py` | Email and phone duplicates, country-code phones, soft-deleted rows |
| `tests/test_employee_passwords.py` | Password length limits, change notice email |
