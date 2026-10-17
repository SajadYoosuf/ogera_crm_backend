# Module: Tasks (cross-lead task list)

The list behind the web Tasks screen and the mobile task list: follow-ups from every lead, filtered by date, status, owner, lead type and search. It reads the same follow-up rows as the lead pipeline ([lead-pipeline.md](lead-pipeline.md)).

Status: in the working tree, not committed yet.

## Files

| File | What it does |
| --- | --- |
| `routes/tasks.py` | `GET /tasks`, `POST /tasks`, `PATCH /tasks/{id}`; builds the task shape |
| `routes/followups.py` | `_followup_response`, `_task_state`, `checked_channel`, `LEGACY_CHANNELS`, the role sets |
| `models/followup_schedule.py` | The follow-up table (`contact_id` may be null for a standalone task) |

## Who sees what

- `admin`, `manager`, `sales_head` see every task and may filter by `assigned_to`.
- Other roles see only tasks assigned to them. Their `assigned_to` filter is ignored.
- Tasks of soft-deleted contacts are left out.
- Standalone tasks (no contact) are included.

## `GET /tasks`

Query: `date_from`, `date_to` (inclusive, at most 62 days apart; both optional), `status` (`open` default, `todo`, `inprogress`, `completed`, `due_today`, `overdue`, `all`), `assigned_to`, `channel`, `contact_type`, `q` (contact name, phone or task title), `include_overdue`, `skip`, `limit` (default 500, max 1000).

Response: `{ items, total, summary }`.

Each item has the activity fields the screens read, plus the contact:

| Field | Meaning |
| --- | --- |
| `id`, `task_id` | The follow-up id |
| `title` | The task's name (may be null) |
| `type` | Activity type. The stored short name `call` is returned as `phone_call`; a missing channel is `other` |
| `notes` | Free text |
| `status` | `pending` (open) or `done` |
| `priority` | `high`, `medium` or `low` |
| `scheduled_date`, `scheduled_time` | The due date and `HH:MM:SS` |
| `assigned_to`, `assigned_to_name` | Owner |
| `task_state` | `pending`, `due_today`, `overdue` or `done` |
| `overdue_days` | Days late (0 unless overdue) |
| `contact_id`, `contact_name`, `contact_phone`, `contact_type` | The lead (`contact_id` is null for a standalone task) |

`summary` is `{ overdue, today, upcoming, done }`. It ignores the date range and status, and follows the other filters.

## `POST /tasks`

Creates a standalone task (no lead): `title`, `due_at`, `assigned_to`, `channel?`, `priority?`, `notes?`. `due_at` cannot be in the past. `201`.

## `PATCH /tasks/{id}`

Body is the follow-up update shape (`status`, `outcome`, `notes`, `due_at`). Assignee or task manager only; a completed task returns `409`.

**Known gap, not fixed yet:** this route does not apply the lead-pipeline rules. It accepts completion without an `outcome`, writes no activity log row, and writes no reschedule history row. The web does not call this route (it uses `PATCH /contacts/{id}/followups/{fid}`), so the gap does not affect the screens. It should be closed before mobile or any other client uses it.

## Tests

- `tests/test_partner_stages.py`: the task list scoped by stage and `status`, and the summary counts.
- `tests/test_mobile_api.py`: the list as the mobile app reads it, and contact-scoped access.
- `tests/test_lead_pipeline_edge_cases.py`: visibility and manager access on follow-ups (the same rules the list uses).
