# Lead tasks list, completion log, reschedule rules and follow-up settings

Date: 2026-11-25 · PRD: `../docs/followups-tasks-prd.md` (workspace) · Web note: `ogera_crm_web/docs/changes/2026-11-25-tasks-page-and-followup-settings.md`

## What changed

**Completing a follow-up writes a linked activity** (`routes/lead_interactions.py`, `PATCH /interactions/{iid}`)
- New optional body fields `actual_activity_type` and `custom_label` (required when the actual type is `other`; 422 otherwise).
- In the same transaction as the status change, a `kind='log'` row is inserted with the actual type, the outcome, the note, `done_by` = actor, `logged_at` = now and `parent_task_id` = the task. The task keeps its planned `activity_type`.
- Completing an already-completed task now returns 400 (prevents duplicate logs on double submit).
- The `task_completed` record_log now stores "Done via WhatsApp" in `new_value`; the timeline shows it as the detail.

**Reschedule** (`PATCH /leads/{id}/interactions/{iid}/reschedule`)
- `due_at` in the past (2-minute grace) → 422. Completed tasks → 400.
- Optional `reason` (255 chars), stored on the `task_rescheduled` record_log after the new time; the timeline detail reads "Moved from 26 Nov 2026, 10:00 to 30 Nov 2026, 10:00 · client travelling" (IST).
- The assignee gets a `followup_rescheduled` notification when someone else moves their task.

**Task responses** gain `is_overdue`, `overdue_minutes` (by the minute, so a task 1 hour late is overdue), `completed_at`, `actual_activity_type`, `actual_custom_label`, `completion_log_id`. `overdue_days` is kept.

**New `GET /lead-tasks`** (`routes/lead_tasks.py`): `bucket=today|overdue|upcoming|completed`, `assigned_to`, `q` (lead name, phone, task title), `skip`, `limit`. Returns `items` (with `lead_name`, `lead_phone`, `lead_status`), `total` and `summary` counts for all four buckets under the same filters. IST day boundaries. Deleted leads are excluded. Today and Overdue overlap for tasks already late today, as the PRD defines them.

**Follow-up settings** (`models/followup_settings.py`, `services/followup_settings.py`, `routes/followup_settings.py`)
- `GET /settings/followups` (any signed-in user), `PATCH /settings/followups` (heads only; writes a `followup_settings_updated` record_log with old and new values of changed fields; unchanged saves write nothing).
- `get_settings(db)` returns built-in defaults when the table or row is missing (lookup inside a SAVEPOINT, so the caller's transaction survives). PATCH answers 503 until the table exists.
- Extra column `status_colors` (JSON) beyond the PRD table: task status colours live here because `enum_values` has no colour column and must not change.
- `allow_staff_reschedule` and `allow_staff_create_followup` are enforced (403 for staff) via `services/lead_access.py`.

**Reminders** (`services/followup_reminders.py`, `scheduler.py`) read the settings per tenant:
- `reminders_enabled` switches all rules off.
- `due_day_only` (default) keeps today's behaviour. `daily_until_done` sends one `followup_digest` per assignee per IST day ("You have 3 follow-ups: 1 due today, 2 overdue"), overdue tasks included for `reminder_max_days` (0 = no limit).
- The due-today job now runs hourly and sends only in each tenant's `due_today_reminder_hour`. Overdue threshold and due-now window come from settings.

**Enums** (`settings_catalog.py`, `routes/settings.py`): new categories `task_status` and `task_priority`, all values locked; adding options to them returns 400.

**Script** `scripts/migrate_followup_settings.sql` (idempotent): creates `followup_settings`, seeds one row, inserts the six task status/priority enum rows if missing. **Not run anywhere.**

## Access / data impact
- No change to `interactions` or `record_logs` schemas. `parent_task_id` (existing, previously unused) is now written on completion logs.
- New table only after the script is run, which needs explicit approval for the live database.
- Default `allow_staff_create_followup` is **true** (the PRD table says false, but its role table and story S5 keep staff creating their own follow-ups; false would have removed today's behaviour). Change in Settings if R4 means otherwise.

## Verification
- New tests: `tests/test_lead_tasks.py` (T1–T7, T9–T13), `tests/test_followup_settings.py` (T8, validation). 33 pass.
- `tests/test_leads_mvp.py`, `test_settings.py`, `test_task_status.py`, `test_followup_reschedule.py`: the same 12 failures before and after this change (legacy contact routes and settings tests that already failed); no new failures. Full suite: the other failing files exercise routers disabled in `main.py`.
- Browser walk-through (J1, J2, J3, J4, T13) against the API running on a throwaway SQLite file with seeded data. Not run against PostgreSQL or the live database; the migration script has not been executed.

## Known issues / not done yet
- Run `scripts/migrate_followup_settings.sql` on a local PostgreSQL copy, then on live after approval. Until then: defaults apply, settings cannot be saved (503), and task status labels fall back to the built-in names.
- `/settings/enums/*` still uses the legacy `get_db` (default schema, not the tenant schema). Status renames and activity type edits go wherever that already points. Pre-existing; not changed here.
- Auto first-response follow-up (`auto_followup_hours`) is stored but nothing uses it yet (PRD non-goal).
- Legacy `GET /tasks` left untouched.
