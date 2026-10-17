# Lead actions from the Tasks screen: complete with a note, reschedule with history

## Why
Mark done and Reschedule from the Tasks screen called a route that does not exist, and rescheduling left no record. A salesperson also needs to log what happened before completing, and see every move on the lead's history.

## What changes
- `PATCH /contacts/{id}/followups/{fid}` completes with `status: "completed"`, `outcome: "done"` and `notes`. The notes become an activity log row linked to the follow-up. The route already did this; the web now calls it.
- `PATCH` with `due_at` writes a row to the new table `followup_reschedule` (old date, new date, who, when) before changing the date. A refused move writes nothing.
- `GET /contacts/{id}/history` returns each move as `event_type: "followup_rescheduled"` with `previous_due_at`, `due_at` and `followup_status: "rescheduled"`.
- `POST /contacts/{id}/activities` refuses a blank `notes` (`422`) and trims the text.
- `GET /tasks` returns `priority` on each item. The summary key `completed` is renamed `done`, as in API.md. The web does not read the summary.
- New model `models/followup_reschedule.py`, registered in `models/__init__.py`. Created on startup by `create_all`.
- Task list items: the name (`title`) is now shown on the web (no backend change needed).

## Out of scope
- `PATCH /tasks/{id}` still skips the lead-pipeline rules. Recorded as a known gap in `docs/modules/tasks.md`.
- Completing with an empty note is still accepted by the API (mobile relies on it).
- The web form still requires the note.

## Verification
- `tests/test_followup_reschedule.py` (2 tests), `tests/test_lead_pipeline_edge_cases.py` (17 tests). Full backend suite: 155 passing; one test (`test_type_counts_respect_visibility`) is intermittent and was not caused by this change.
- Live end-to-end run with the web app on desktop, tablet and phone: lead pipeline flow and task flows (see the web change notes).
