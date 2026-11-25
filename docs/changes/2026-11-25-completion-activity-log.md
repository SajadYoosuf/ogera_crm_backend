# Follow-up completion writes an activity entry

**Date:** 2026-11-25
**Web counterpart:** none needed; the web already sends "Done via" and the note.

## What changed

- `routes/lead_interactions.py`, `PATCH /interactions/{iid}`: `TaskUpdate` accepts `actual_activity_type` and `custom_label`. When a lead follow-up moves to `completed` for the first time, an activity entry (`kind="log"`) is written with the "Done via" type (or the planned type when none is given), the custom label for "other", the title, the completion note, the outcome, the completing user, and a link to the follow-up (`parent_task_id`). Completing it again writes nothing more. Choosing "other" without a label returns 400.

## Access / data impact

No role change. Writes one activity row per first completion, visible to anyone who can see the lead's activity log, the same as other activity entries.

## Verification

- `tests/test_completion_activity_log.py` (2 tests) passes: a completion writes one entry with type, note, outcome, completer and link; completing twice writes one entry.
- Full backend suite: `210 failed, 312 passed, 2 xfailed, 14 errors`. Failing set identical to the previous gate run.
- `py_compile` and `import main`: pass.
- Live: a completion of a follow-up made after the server reloaded produced the linked entry (task 13 → activity 15).

## Known issues or not done yet

- `test_lead_tasks.py::test_complete_call_as_whatsapp_writes_linked_log` expects more (completed_at, timeline label) and still fails; not part of this change.
- Completions made before the server reloaded have no linked entry.
