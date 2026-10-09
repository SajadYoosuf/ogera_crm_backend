# Tasks list for day / week / month views, and outcome on completion

## Why
Follow-ups scheduled from a lead only show inside that lead's panel. Staff and managers need one list of tasks by day, week and month, per employee, and a finished task must leave a record of what happened.

## What changes
- `GET /tasks` (new `routes/tasks.py`): scheduled follow-ups across leads, filtered by date range, assignee, status, type and contact search. Sales staff always get only their own tasks; admin / manager / sales_head can filter by employee. Returns `items` (with contact name, phone, type), `total` and `summary` counts (overdue, today, upcoming, done in range).
- `PATCH /contacts/{id}/activities/{aid}` with `status: "done"` now requires `outcome` (text). In the same transaction it creates a finished activity log entry (same type, the outcome as notes) linked to the task through the new `task_id` column. Only the assignee or a privileged role can complete or reschedule a task.
- `_task_state` now respects the time: a 10:00 task is overdue at 3 pm the same day.
- `lead_activities.task_id` (nullable, FK to `lead_activities.id`): `scripts/migrate_lead_activities_task_id.sql` (manual, `create_all` does not alter tables).
- `API.md` updated.

## Out of scope
Push reminders, recurring tasks, bulk reassign, mobile screens.

## Verification
Import the app and smoke-test with FastAPI `TestClient` against a throwaway SQLite database (no backend test suite exists yet).

## As built (deviations)
- `TASK_MANAGER_ROLES` = admin, manager, sales_head (shared by the task list and the PATCH permission check in `routes/lead_activities.py`).
- `_task_state` became the public `task_state`, `_response` became `activity_response` (used by `routes/tasks.py`).
- The `assigned_to` index is replaced by `(assigned_to, status, scheduled_date)` in the migration.
- Verified with a throwaway SQLite smoke test (list filters, staff scoping, outcome required, outcome log created, 403 and 409 cases). Not run against MySQL: **run `scripts/migrate_lead_activities_task_id.sql` before deploying**. There is still no pytest suite.
