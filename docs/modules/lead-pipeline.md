# Module: Lead pipeline (follow-ups, activity log, history)

The part of the CRM where a salesperson works one lead: schedules a follow-up, logs what happened, completes or reschedules a follow-up, and reads the lead's timeline. The web lead panel and the Tasks screen both use these routes.

Status: in the working tree, not committed yet. Tables: `followup_schedule`, `activity_log`, `followup_reschedule` (new in this change, created on startup by `create_all`; no migration script is needed for it).

## Files

| File | What it does |
| --- | --- |
| `routes/followups.py` | Follow-up routes: list, schedule, complete, reschedule |
| `routes/lead_activities.py` | Activity log routes: list and log an activity |
| `routes/history.py` | The combined timeline for one lead |
| `schemas/followup_schedule.py` | Follow-up request and response shapes, `Outcome`, `FollowupStatus` |
| `schemas/activity_log.py` | Activity log shapes, `HistoryItem` (timeline event) |
| `models/followup_schedule.py` | Follow-up table |
| `models/activity_log.py` | Activity log table |
| `models/followup_reschedule.py` | One row per due-date move (old date, new date, who, when) |
| `contact_access.py` | Who may see a lead (`get_visible_contact`) |
| `settings_catalog.py` | Activity types that may be scheduled or logged |

## Routes

| Method and path | Who may call | Result |
| --- | --- | --- |
| `GET /contacts/{id}/followups` | Anyone who can see the lead | The lead's follow-ups, with `task_state` and `overdue_days` |
| `POST /contacts/{id}/followups` | Anyone who can see the lead | Schedules a follow-up (`201`) |
| `PATCH /contacts/{id}/followups/{fid}` | Assignee, or admin / manager / sales_head | Completes (`status: completed` + `outcome`), moves to `todo` / `inprogress`, or reschedules (`due_at`) |
| `GET /contacts/{id}/activities` | Anyone who can see the lead | Logged activities, newest first |
| `POST /contacts/{id}/activities` | Anyone who can see the lead | Logs something that happened (`201`) |
| `GET /contacts/{id}/history` | Anyone who can see the lead | Timeline: stage moves, follow-ups, activities, reschedules, owner changes |

## Rules

**Scheduling a follow-up**
- `due_at` cannot be in the past (a 5-minute grace applies). `422`.
- The assignee cannot be an admin or manager (rule 1). `400`.
- `channel` must be an active activity type in Settings. `internal_note` cannot be scheduled. `400`.
- `title` is optional and shown as the task name.

**Completing a follow-up**
- `outcome` is required and must be `done`, `not_now` or `no_answer`. Free text is refused. `422`.
- `notes` is stored as the activity's text. The web always sends `outcome: "done"`.
- Completing writes one activity log row, linked by `followup_id`, in the same transaction.
- `not_now` and `no_answer` also create a retry follow-up after `RETRY_FOLLOWUP_DAYS` (see `config.py`).
- A completed follow-up cannot be completed again or rescheduled. `409`.

**Rescheduling a follow-up**
- Send only `due_at`. The new time cannot be in the past. `422`.
- Each successful move writes one `followup_reschedule` row with the old and new due date and the person who moved it.
- A refused move writes nothing (covered by a test).

**Logging an activity**
- `activity_type` is required. `notes` is required and trimmed; a blank note is refused. `422`.
- `done_by` can be set only by admin / manager (log on behalf of another employee).

**Who may see or act**
- A lead the caller cannot see returns `404` on every route, including history. The caller cannot tell it exists.
- Completing or rescheduling by someone other than the assignee returns `403`, unless the caller is admin, manager or sales_head.

**Timeline (`/history`)**
- Events are sorted newest first.
- `followup_rescheduled` carries `previous_due_at` (the old date), `due_at` (the new date) and `followup_status: "rescheduled"`.

## Known gaps

- `PATCH /tasks/{id}` (see [tasks.md](tasks.md)) does not use these rules. It can complete a follow-up without an outcome, writes no activity log row, and writes no reschedule row.
- The API accepts an empty `notes` on completion (`outcome` alone is enough). The web form requires the note. Mobile completes with `outcome` only, so the API keeps accepting it.

## Tests

- `tests/test_followup_reschedule.py`: a reschedule appears in history with the old date; completing logs the typed note.
- `tests/test_lead_pipeline_edge_cases.py`: 17 cases covering the rules above (past dates, role limits, `409` on a second completion, visibility, retry follow-up, history ordering).
- `tests/test_partner_stages.py`, `tests/test_mobile_api.py`: follow-ups as seen by the partner pipeline and the mobile app.
