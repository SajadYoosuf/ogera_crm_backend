# Activity log and follow-up schedule (v2 of lead activities)

## Why
The lead panel needs two separate actions: log something that already happened, and schedule a follow-up task assigned to a staff member, with reschedule and mark-done. The first version (one table with `kind`, `subtype`, `due_at`) did not model assignee, date and time separately, or who completed a task.

## What changes
- `lead_activities` is rebuilt (it had 0 rows): `type`, `notes`, `status` (`pending`/`done`), `scheduled_date`, `scheduled_time`, `assigned_to`, `completed_at`, `completed_by`, optional GPS, `created_by`, `created_at`. `scripts/migrate_lead_activities_v2.sql` drops and recreates it.
- `schemas/lead_activity.py`, `routes/lead_activities.py` rewritten; API.md updated.
  - `POST /contacts/{id}/activities`: a log entry (notes required, done immediately) or a scheduled task (`scheduled_date` + `assigned_to`, `status: "pending"`; date not in the past; no internal notes).
  - `PATCH /contacts/{id}/activities/{id}`: `{status: "done"}` marks a pending task done (server sets time and user), or `{scheduled_date, scheduled_time?}` reschedules a pending task.
  - `GET`: newest first, each item with `task_state` (`pending`, `due_today`, `overdue`, `done`) and `overdue_days`.
- Removed: `subtype`.

## Out of scope
Employee-only task view, notifications, deleting activities.

## Verification
Schema checks by direct call; POST a log, a task, PATCH done and reschedule; confirm a past date and an internal-note schedule are rejected.
