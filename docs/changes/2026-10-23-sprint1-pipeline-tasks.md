# Sprint 1 — Lead Pipeline Task System Redesign + Bug Fixes (backend)

## Why
The follow-up/task system was missing key fields (created_by, pending status, audit log) and had
four bugs: tasks.py completion didn't write activity_log; ActivityLogForm dropped date/time
silently; retry follow-ups had no deduplication; and a single-contact stage resolution caused an
N+1. This sprint fixes all of them and adds the task system improvements planned for MVP.

## What changes
- `models/followup_schedule.py` — add `created_by` FK column
- `models/followup_edit_log.py` — new table: one row per field changed on a followup/task
- `models/__init__.py` — register FollowupEditLog
- `schemas/followup_schedule.py` — add `pending` to FollowupStatus; add `actual_channel` and
  `created_by_id` to update/response schemas
- `settings_catalog.py` — add `meeting` and `email` to DEFAULT_ENUMS[ACTIVITY_TYPE]
- `routes/followups.py` — set created_by on create; creator-only reschedule gate; actual_channel
  at completion; retry dedup; write followup_edit_log on field changes; add pending status
- `routes/tasks.py` — fix completion to write activity_log (Bug 1); set created_by on create;
  same creator-only reschedule gate; write followup_edit_log; add pending status
- `routes/history.py` — include followup_edit_log events in the timeline
- `schemas/activity_log.py` — add "followup_edit" event_type + field_name/old_value/new_value
- `scripts/migrate_sprint1_pipeline.sql` — ADD COLUMN created_by + CREATE TABLE followup_edit_log
  + INSERT two new activity_type enum rows

## Out of scope
- Contact-creation auto-follow-up (Sprint 2)
- Notification/reminder system (Sprint 4)
- Mobile FollowupScheduleForm changes (separate mobile commit)
- Dead table cleanup

## Verification
```bash
cd ogera_crm_backend
pytest tests/test_lead_pipeline_edge_cases.py -v
pytest tests/test_mobile_api.py -v
```
Manual: complete a follow-up via PATCH /tasks/{id} → check activity_log row exists.
Manual: reschedule a follow-up as a different employee → expect 403.
Manual: schedule two follow-ups for same contact and complete both with no_answer → confirm only
one retry follow-up created (not two).
