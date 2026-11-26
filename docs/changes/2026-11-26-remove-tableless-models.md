# Remove models with no PostgreSQL table; work from `interactions`

## What changed

**Removed** (no table in the Postgres `public` or `ventino` schema):

- Models: `Sale`, `Product`, `ProductOption`, `contact_interested_products` (link table), `Expense`,
  `FollowupEditLog`, `FollowupReschedule`, `CompanySetting`, `StaffAttendance`, `ActivityLog`,
  `FollowupSchedule` (and the `Contact.followup_schedules` relationship).
- Schemas: `schemas/sale.py`, `schemas/product.py`, `schemas/followup_schedule.py`.
- Script: `scripts/import_products.py`.
- `settings_catalog.py`: `DEFAULT_COMPANY`, `get_company()`, `set_company()` (no callers; the company profile
  is on `public.tenants`), and the company-settings part of `seed_defaults()`.
- Tests: `tests/test_expenses.py`, `tests/test_sales_products_independence.py`, the attendance tests, and two
  tests in `tests/test_partner_stages.py` that called the removed `/followups` and `/tasks` routes.
  The staff-location tests moved from `tests/test_attendance.py` to `tests/test_staff_location.py`.

**Routes removed** (files deleted and unwired from `main.py` and `routes/__init__.py`):

- Were serving requests, now return 404: the routers in `routes/dashboard.py`, `routes/attendance.py`,
  `routes/expenses.py`, `routes/products.py`, `routes/sales.py` and `routes/tasks.py`.
- Already disabled: `routes/followups.py`, `routes/history.py`, `routes/lead_activities.py`.
- A search of the web app found no calls to these paths. The mobile app was not checked.

**Now using `Interaction`:**

- Inactivity reminder (`run_48h_rule`): a contact is active when it has an `interactions` row with
  `entity_type='contact'` and `kind='log'` newer than the cutoff. The cutoff is UTC.
- Daily digest (`run_daily_digest`): counts `kind='task'` rows with status `todo` or `inprogress`, due within
  today's IST day, on contacts and leads that are not soft-deleted.
- Contact import (`contact_import.py`, `run_import`): each new contact gets a task interaction
  (`phone_call`, priority `medium`, status `todo`, due in 24 hours). Before, it tried to write a
  `followup_schedule` row.
- Activity-type usage counts (`usage_counts`): counts `kind='log'` rows on live contacts and leads. Before,
  only contact activity was counted.

**Scheduler fix** (`scheduler.py`): the inactivity check and the daily digest now run once per active tenant,
inside that tenant's schema. Before, they used a session on the default search path, which cannot see the
tenant tables on Postgres, so both jobs failed and only logged an error.

New tests: `tests/test_activity_interactions.py`.

## Access / data impact

- No migration and no data change by this commit.
- **Behaviour change:** the inactivity reminder and the daily digest start working. The inactivity rule notifies
  the assigned employee and every admin and manager for each assigned contact with no recent activity. On
  2026-11-26 the live database had no assigned live contacts, so nothing would be sent at that time.

## Verification

- The app imports and ORM mappers and foreign keys resolve (104 routes).
- Read-only catalog check: no foreign key in the live database points at the removed tables, and every live table
  still has a model.
- Both scheduled jobs ran against the live database for the `ventino` schema in read-only transactions: no
  errors, 0 notifications. The activity-type usage query also ran read-only.
- `pytest tests/test_activity_interactions.py`: 5 passed (SQLite).
- Full suite with `--ignore=tests/test_contacts_module.py`: 143 failed, 337 passed, 2 xfailed after the
  follow-up move; 148 failed, 331 passed before it. Two tests in `tests/test_leads_mvp.py` failed only in the
  after run; both passed in 4 of 4 runs alone. Several tests in this suite are unreliable.
- Not checked in a running server or browser. Not deployed. No real notification or push was sent.

## Known issues or not done yet

- Push notifications: `dispatch_notification_push` looks up devices with a session on the default search path,
  so on Postgres the lookup fails and no push is sent. In-app notifications are unaffected.
- `check_dedup` compares an IST cutoff with UTC `created_at`, so the 24-hour window is about 18.5 hours.
- Still no table: `StaffLocation` / `StaffLocationHistory` (`/staff/location` routes).
- On Vercel the in-process scheduler is not reliable, because functions do not stay running.
- `schemas/lead_stage.py` is unused and was left in place.
- `tests/test_contacts_module.py` leaves `app.dependency_overrides` set, which breaks many later tests.
- Some tests still call removed routes and fail.
- Old SQL files under `scripts/` still create the removed tables.
