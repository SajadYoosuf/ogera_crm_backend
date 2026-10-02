# Employee CRUD, managed roles and credentials email

## Why
The CRM had no way to manage staff. Admins/managers need to create employees (name, email, phone, role, password) with standard CRM roles such as tele caller, and to add new roles themselves. New employees should receive their login details by email.

## What changes
- `routes/employees.py` (new): `POST/GET/PUT/DELETE /employees`. Admin/manager only. Only an admin can create, promote, edit or delete a manager/admin. Duplicate email or phone returns 409. Password stored as an argon2 hash.
- `routes/roles.py`, `models/role.py`, `schemas/role.py` (new): managed roles table with `/roles` CRUD (admin only for writes). Seven built-in system roles are seeded at startup. `employees.role` is now a slug linked to `roles.slug`.
- `schemas/employee.py`, `schemas/enums.py`, `models/employee.py`: stricter validation (phone, password), role is a slug, `RoleEnum` holds the built-in role names.
- `auth.py`: `require_roles(...)` dependency.
- `mailer.py` (new): sends login details to the new employee by SMTP and reports the outcome in the create response (`credentials_email`). The recipient domain is checked in DNS first.
- `middleware.py`: request/response logging with passwords and tokens masked; unhandled exceptions now return a JSON 500 that still carries CORS headers.
- `config.py`: token lifetime default 5 days; SMTP settings; `load_dotenv()`.
- `scripts/`: `migrate_employee_roles.sql`, `migrate_dynamic_roles.sql`, updated `create_tables.sql`.
- New env variables: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_FROM_NAME`, `SMTP_SECURITY`, `APP_LOGIN_URL` (optional `ACCESS_TOKEN_EXPIRE_MINUTES`).

## Out of scope
- Per-role permissions: custom roles are labels only, only `admin`/`manager` carry access rules.
- Authentication on `/contacts/*` (still open).
- Changes to the web and mobile apps (the web app uses different role names, see below).
- `API.md`, which lives one folder above this repo.

## Verification
- In-memory SQLite tests with a stubbed mailer: roles CRUD and role assignment (30 checks), employee authorization (23), phone uniqueness (11), log masking, CORS on 500s.
- One real test email sent through Gmail SMTP to the sender's own mailbox.
- Endpoints were not exercised against the real MySQL database. Both migrations were applied to the dev database.

## Deviations / notes
- The web app expects roles `admin`, `sales_head`, `sales_staff`; a `manager` would not pass its role checks. Decision pending.
- Run `scripts/migrate_employee_roles.sql` then `scripts/migrate_dynamic_roles.sql` on existing databases.
- Gmail accepts any recipient and bounces unknown mailboxes later, so `credentials_email.sent=true` is not proof of delivery.
