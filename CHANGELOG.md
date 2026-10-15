# Changelog

## 2026-10-15 — `5d54032` (branch `phase1-redesign`)

**Swagger descriptions for every API; password length 6-15 and change emails**

Author: sajad yoosuf
Previous commit on this side: `931d39d` Remove heartbeat endpoint and employee_presence model (2026-10-15)

### Summary
Every route has a Swagger summary and description. Passwords for new and admin-edited employees must be 6 to 15 characters. An employee is emailed when an admin changes their password. Module guides: `docs/modules/authentication.md`, `docs/modules/employee-management.md`.

### Files
| Status | File | Change |
| --- | --- | --- |
| Modified | `routes/auth.py`, `routes/contacts.py`, `routes/lead_activities.py`, `routes/lead_sources.py`, `routes/performance.py`, `routes/products.py`, `routes/roles.py`, `routes/sales.py`, `routes/staff_locations.py`, `routes/tasks.py`, `routes/employees.py` | Swagger summary and description on each route |
| Modified | `main.py` | Summary and description for `GET /` and `GET /health` |
| Modified | `config.py` | `PASSWORD_MIN_LENGTH` (6), `PASSWORD_MAX_LENGTH` (15) |
| Modified | `schemas/employee.py` | Password length from config; the old 8-character minimum is gone |
| Modified | `routes/employees.py` | Sends the password-changed notice after an admin changes a password |
| Modified | `mailer.py` | `send_password_changed_notice` |
| Added | `tests/test_employee_passwords.py` | Length limits and the change notice |
| Modified | `rule.md` | Rule 5: password rules and change notices |

### Notes
The forgot-password feature and the settings feature are not in this commit. Three contact routes (`contact_map_pins`, `contact_type_counts`, `list_stage_types`) get their Swagger text when `routes/contacts.py` from the contacts work is committed.

## 2026-10-15 — `931d39d` (branch `phase1-redesign`)

**Remove heartbeat endpoint and employee_presence model**

Author: sajad yoosuf
Previous commit on this side: `18ffeb3` Employees: staff can list employees, hide admins, add heartbeat (2026-10-15)

### Summary
`POST /auth/heartbeat` and the presence table are removed. The table was empty and unreferenced, and was dropped from the database.

### Files
| Status | File | Change |
| --- | --- | --- |
| Modified | `routes/auth.py` | Heartbeat route and its response model removed |
| Deleted | `models/employee_presence.py` | `EmployeePresence` model removed |
| Modified | `models/__init__.py` | `EmployeePresence` export removed |
| Modified | `tests/test_mobile_api.py` | Heartbeat test removed |

## 2026-10-15 — `18ffeb3` (branch `phase1-redesign`)

**Employees: staff can list employees, hide admins, add heartbeat**

Author: sajad yoosuf
Previous commit on this side: `99b9bd3` Redesign the Employees page to the design and add a Role filter

### Summary
Any signed-in employee can read the employee list and a single employee, but admin accounts are never returned, and managers do not see other managers. The phone duplicate check ignores the country code and spacing. `is_field_staff` is removed. The backend test-audit skill is added. Module guides for authentication and employee management are in `docs/modules/`.

### Files
| Status | File | Change |
| --- | --- | --- |
| Modified | `routes/employees.py` | List and get open to signed-in employees, with hidden roles; phone match on last 10 digits; `is_field_staff` removed from the allowed fields |
| Modified | `models/employee.py` | `is_field_staff` removed |
| Modified | `schemas/employee.py` | `is_field_staff` removed from create and update |
| Modified | `scripts/create_tables.sql`, `scripts/seed.py` | `is_field_staff` removed |
| Modified | `tests/test_mobile_api.py` | Employee list visibility tests |
| Added | `tests/test_employee_duplicates.py` | Duplicate email and phone tests, including soft-deleted rows and country codes |
| Added | `.claude/skills/backend-test-audit/SKILL.md` | Project skill for auditing backend changes |
| Modified | `rule.md` | Rule 3: employee list hides admin accounts |

### Notes
The DB column `employees.is_field_staff` still needs `ALTER TABLE employees DROP COLUMN is_field_staff;` on databases that have it. The code no longer reads or writes it.

## 2026-10-12 — `c944450` (branch `phase1-redesign`)

**Redesign the Contacts page to the design and add Sort by**

Author: sajad yoosuf
Previous commit on this side: `4f9501f` Connect the mobile app to the FastAPI backend (2026-10-09)

### Summary
`GET /contacts` takes a `sort` parameter for the Contacts "Sort by" control. Notes: `docs/changes/2026-10-12-contacts-sort.md`.

### Files
| Status | File | Change |
| --- | --- | --- |
| Modified | `routes/contacts.py` | `sort` query: `newest` (default), `oldest`, `name_asc`, `name_desc`; `CONTACT_ORDERINGS` map with an id tie-break |

### Notes
Only the sort change from this commit is in this side's history; other backend work from the same period is still uncommitted. Restart the server to pick up the parameter. The web side changed in the same commit.

## 2026-10-09 — `a207130` (branch `phase1`)

**Add Tasks screen (day, week, month) and require an outcome when a follow-up is done**

Author: sajad yoosuf
Previous commit on this side: `99798ab` docs: update changelog for addee71 (2026-10-09)

### Summary
New `GET /tasks` feeds the Tasks screen, and marking a follow-up done now logs its outcome as an activity. Notes: `docs/changes/2026-10-09-tasks-calendar-outcome.md`.

### Files
| Status | File | Change |
| --- | --- | --- |
| Added | `routes/tasks.py` | `GET /tasks`: date range, employee, status, type, search; staff scoped to their own; summary counts |
| Modified | `routes/lead_activities.py` | Outcome required on done and logged as an activity; only the assignee or a manager can change a task; time-aware `task_state` |
| Modified | `schemas/lead_activity.py` | `outcome` on update, `task_id` on response, task list schemas |
| Modified | `models/lead_activity.py` | `task_id` column, new assignee index |
| Added | `scripts/migrate_lead_activities_task_id.sql` | Adds `task_id` and replaces the assignee index |
| Modified | `routes/__init__.py`, `main.py` | Register the tasks router |
| Added | `docs/changes/2026-10-09-tasks-calendar-outcome.md` | Change notes |

### Notes
Run `scripts/migrate_lead_activities_task_id.sql` before deploying. `API.md` is updated. The same commit changed the web side. Checked with a SQLite smoke test only; there is no pytest suite.

## 2026-10-09 — `addee71` (branch `phase1`)

**Add contact import and export (CSV, Excel, PDF) for backend and web**

Author: sajad yoosuf
Previous commit on this side: `d26872d` docs: update changelog for a47a286 (2026-10-08)

### Summary
Contacts can be imported from CSV/XLSX (analyze, then commit) and exported as CSV, Excel or PDF with the list filters. Notes: `docs/changes/2026-10-08-contact-import-export.md`.

### Files
| Status | File | Change |
| --- | --- | --- |
| Added | `contact_import.py` | Parses sheets, maps headers, validates rows, classifies ready/warning/duplicate/error |
| Added | `contact_export.py` | Builds CSV, XLSX and PDF output |
| Added | `docs/changes/2026-10-08-contact-import-export.md` | Change notes |
| Modified | `requirements.txt` | Adds `python-multipart`, `openpyxl`, `reportlab` |
| Modified | `routes/contacts.py` | Shared list filters, `created_from`/`created_to`, new `/export`, `/import/template`, `/import/analyze`, `/import/commit` routes |

### Notes
Run `pip install -r requirements.txt`. Import is admin/manager only; other roles export only their own contacts. PDF needs a Malayalam font for Malayalam text. Same commit also touched web and root `API.md`.

## 2026-10-08 — `a47a286` (branch `phase1`)

**Add partner profession type enum and partner type picker in contact form**

Author: sajad yoosuf
Previous commit on this side: `849413e` docs: update changelog for cc64a9e (2026-10-07)

### Summary
Partner type is now a fixed `ProfessionType` enum that contacts can set on create and update. Notes: `docs/changes/2026-10-08-partner-profession-type.md`.

### Files
| Status | File | Change |
| --- | --- | --- |
| Added | `docs/changes/2026-10-08-partner-profession-type.md` | Change notes |
| Modified | `routes/contacts.py` | New `GET /contacts/profession-types`; `partner_profile` handled on create and update (partner contacts only); `profession_type` filter takes the enum and matches legacy text |
| Modified | `schemas/__init__.py` | Export `ProfessionType` |
| Modified | `schemas/contact.py` | `partner_profile` on `ContactCreate`/`ContactUpdate`; list rows use the enum |
| Modified | `schemas/enums.py` | New `ProfessionType` (plumber, engineer, interior_designer, architect, contractor, electrician, other) with `normalise()` |
| Modified | `schemas/partner_profile.py` | `profession_type` is the enum; responses map legacy text to a member |
| Modified | `scripts/import_professionals.py` | Writes lowercase profession values |

### Notes
No migration: the column stays VARCHAR and old rows are normalised on read (unknown text becomes `other`, which pickers do not offer). `API.md` updated. The web contact form uses this in the same commit; mobile is not changed yet.

## 2026-10-07 — `4914a08`..`cc64a9e` (branch `phase1`)

**Lead followup: consumer stages, verification gate, activity log and follow-up tasks, configurable CORS**

Author: sajad yoosuf
Previous commit on this side: `48b3df0` Add verified / unverified filter to contacts (2026-10-05)

### Summary
Backend support for the Lead Followup page. Notes: [consumer-lead-stages](docs/changes/2026-10-05-consumer-lead-stages.md), [lead-activities](docs/changes/2026-10-05-lead-activities.md), [activity-followup-schedule](docs/changes/2026-10-06-activity-followup-schedule.md), [offline-location-on-create](docs/changes/2026-10-06-offline-location-on-create.md), [cors-origins](docs/changes/2026-10-07-cors-origins.md).

### Files
| Status | File | Change |
| --- | --- | --- |
| Modified | `schemas/enums.py`, `models/lead_stage.py` | Stages `first_call`, `connected`, `negotiating`, `repeat_customer` added; `referral` kept |
| Modified | `routes/contacts.py` | New contacts start as `new_lead`; `POST /contacts/{id}/stages` returns 409 with `missing_fields` when an unverified lead leaves `new_lead` (Lost allowed); offline coordinates required on create only |
| Modified | `verification.py` | Offline contacts no longer need a GPS location to be verified (SQL and Python rules kept in sync) |
| Added | `models/lead_activity.py`, `schemas/lead_activity.py`, `routes/lead_activities.py` | `lead_activities` table and `GET/POST/PATCH /contacts/{id}/activities` (log, schedule, mark done, reschedule) |
| Modified | `models/__init__.py`, `routes/__init__.py`, `main.py` | Register the new model and router; CORS uses `CORS_ORIGINS` |
| Modified | `config.py` | `CORS_ORIGINS` environment variable (comma separated, default `http://localhost:3000`) |
| Modified | `scripts/seed.py`, `scripts/backfill_stages.py` | Stage list and descriptions; backfill starts everyone as `new_lead` |
| Added | `scripts/migrate_lead_stage_values.sql`, `scripts/migrate_lead_activities_v2.sql` | Widen the stage ENUM; recreate `lead_activities` |
| Added | `scripts/reassign_admin_leads.py`, `scripts/reset_aware_to_new_lead.py` | One-off data fixes (dry run by default, `--commit` to write) |
| Added | `docs/changes/*` | Change notes (five files) |

### Notes
- Applied to the live database on 2026-10-06: the stage ENUM widening, the `lead_activities` rebuild (table was empty), 531 admin-assigned contacts moved to sajad yoosuf, 127 contacts moved from `aware` to `new_lead`.
- A database used by another deployment (for example the Render service, if it points elsewhere) needs the two SQL files run once.
- Set `CORS_ORIGINS` on the Render service to every web address that calls the API. Render's start command should be `python -m uvicorn main:app --host 0.0.0.0 --port $PORT`.
- `API.md` documents the new endpoints and the 409 rule. The web side of the same work is in `ogera_crm_web/CHANGELOG.md`.

## 2026-10-05 — `48b3df0` (branch `phase1`)

**Add verified / unverified filter to contacts**

Author: sajad yoosuf
Previous commit on this side: `d39dc1b` Add verified / unverified status to contacts (2026-10-05)

### Summary
`GET /contacts` accepts `verified=true|false`, filtered in SQL so totals and paging stay correct. Notes: [docs/changes/2026-10-05-contact-verification.md](docs/changes/2026-10-05-contact-verification.md).

### Files
| Status | File | Change |
| --- | --- | --- |
| Modified | `verification.py` | Adds `verified_clause()`, the SQL twin of `missing_fields()` |
| Modified | `routes/contacts.py` | New `verified` query parameter on the list route |
| Modified | `docs/changes/2026-10-05-contact-verification.md` | Notes updated |

### Notes
No migration. `verified_clause()` and `missing_fields()` must be changed together. Checked on an in-memory SQLite database (216 contacts, same result as the Python rule); not run against MySQL. The same commit also touched web and `API.md`.

## 2026-10-05 — `d39dc1b` (branch `phase1`)

**Add verified / unverified status to contacts**

Author: sajad yoosuf
Previous commit on this side: `c93ee92` docs: update changelog for 2ce70d2 (2026-10-03)

### Summary
Contacts now report `is_verified` and `missing_fields`, computed on read. Notes: [docs/changes/2026-10-05-contact-verification.md](docs/changes/2026-10-05-contact-verification.md).

### Files
| Status | File | Change |
| --- | --- | --- |
| Added | `verification.py` | `missing_fields(contact)` holds the rules: name, phone, district, address, lead source; offline leads also GPS and creator; consumers need house stage, partners need work area |
| Modified | `schemas/contact.py` | `is_verified` and `missing_fields` added to `ContactListItem` and `ContactResponse` |
| Modified | `routes/contacts.py` | Fills the two fields in `GET /contacts` and `GET /contacts/{id}`; the list also eager-loads `consumer_profile` |
| Added | `docs/changes/2026-10-05-contact-verification.md` | Change notes |

### Notes
No migration or new column. `API.md` at the repo root documents the fields. The same commit also touched web. The route itself was not run against a live database; the rules were checked with sample contacts.

## 2026-10-02 — `2ce70d2` (branch `phase1`)

**Restructure repo into backend / web / mobile folders**

Author: sajad yoosuf
Previous commit on this side: none (first commit on this side)

### Summary

The FastAPI backend is tracked in the shared repo for the first time, under `ogera_crm_backend/`. Before this it sat untracked beside the web app, with an empty repo of its own that has been removed.

### Files

| Status | File | Change |
| --- | --- | --- |
| Added | `main.py`, `auth.py`, `config.py`, `database.py`, `middleware.py`, `mailer.py`, `requirements.txt` | App entry point, JWT auth, settings, MySQL engine, request logging, employee-credential email. |
| Added | `models/` (10 files) | SQLAlchemy models: contacts, partner and consumer profiles, employees, roles, lead sources and stages, staff locations. |
| Added | `routes/` (7 files) | Auth, contacts, employees, lead sources, roles and staff-location routes. |
| Added | `schemas/` (9 files) | Pydantic request and response models. |
| Added | `scripts/` (13 files) | `create_tables.sql`, SQL migrations, seed, and data import scripts (contacts, Excel leads, professionals). |
| Added | `docs/changes/` | Change notes for the employee and contacts work. |
| Added | `.gitignore` | Ignores `.env*`, keys, `.venv`, `__pycache__` and the `contacts_*` / `professionals_*` JSON exports. |

### Notes

- Customer data files in this folder (`*.xlsx`, `*.pdf`, `buyers_register.*`, `contacts_*.json`, `professionals_*.json`) and `.env` are deliberately not in git; the root `.gitignore` covers them.
- There are no migration tracking tables; run the SQL in `scripts/` by hand, and check data migrations before re-running them.
- Pairs with the web entry for `2ce70d2`.
