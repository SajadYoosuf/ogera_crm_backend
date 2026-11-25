# Follow-up event notifications and live stream fix

**Date:** 2026-11-25
**Web counterpart:** `ogera_crm_web` change note `docs/changes/2026-11-25-tasks-action-menu.md`

## What changed

- `routes/lead_interactions.py`: follow-up events now write in-app notifications.
  - Create: the assignee gets `followup_assigned` (not when they create it themselves); admins and managers get `followup_created`.
  - Status change: assignee and admins/managers get `followup_status`, or `followup_completed` when completed. Only when the status actually changes.
  - Reschedule: assignee and admins/managers get `followup_rescheduled`, with the new time in IST.
  - Reassign: new assignee gets `followup_assigned`; previous assignee and other admins/managers get `followup_reassigned`.
  - Delete: assignee and admins/managers get `followup_deleted`.
  - The person who made the change never gets their own notification.
- `services/lead_access.py`: adds `head_employee_ids(db)`, which the activity-log notifications already called but did not exist. Without it, a staff member logging an activity would fail.
- `middleware.py`: the request-logging middleware no longer drains `text/event-stream` responses. It used to wait for the whole stream, so the live notification stream sent nothing until it closed.
- `tests/test_task_notifications.py` (new): 7 tests covering who receives each event.
- `tests/test_request_logging.py` (new): starts a real HTTP server and checks the first stream event arrives before the stream ends.

## Access / data impact

No role or permission changes. Notifications go to users who already see the lead: the assignee and users whose role has `see_all_records` (admin and manager in production). No schema change.

## Verification

- `tests/test_task_notifications.py`, `tests/test_request_logging.py`, `tests/test_notification_stream.py`: 10 passed.
- Full backend suite compared with a clean `HEAD` checkout: 19 tests that failed at `HEAD` now pass. One test, `test_whatsapp_reply_validation`, fails only in the working tree. It is not caused by this change and is being looked at separately.
- Live check on the local server before the middleware fix: the notification row was written, but the stream did not push it. After the fix, the live re-check was not completed.
- `import main`: passes.

## Known issues or not done yet

- `test_whatsapp_reply_validation` fails in the working tree but passes at `HEAD`.
- Other changes in the working tree (router registration, `PUBLIC_API_URL`, `task_status`/`task_priority` enums) are not part of this commit.
- `pyproject.toml` and `uv.lock` list only `fastapi`; the app's real dependencies are in `requirements.txt`. `uv sync --frozen` removes them from the environment, so use `requirements-dev.txt` instead.
