# 2026-11-25 — Lead timeline shows who an assignment went to (backend)

Assignment rows in the lead timeline said only "Lead assigned" plus the actor. The target employee was stored
in `record_logs.new_value` as an id but was never resolved, so nobody could see who the lead was given to.

## What changed
- `routes/lead_interactions.py` — `GET /leads/{lead_id}/timeline` now returns a `detail` field per row:
  - `assigned` and `task_reassigned`: `"Assigned to <name>"`; `"Unassigned"` when the new value is empty;
    `"Assigned to an employee"` if the id no longer resolves to an employee.
  - every other action: `null`.
- Employee names for assignment rows are looked up in the same query as the actors, so there is no extra round trip per row.

## Response shape
Each timeline item now has `detail: string | null`. The other fields are unchanged.

## Tests
`tests/test_leads_mvp.py::test_timeline_records_status_and_assignee_changes_and_detail_has_assignee_name`
checks the assignment row and its detail text. **It did not run in this change:** the test's fixture fails at
lead creation ("Only admin or manager can assign leads to a specific person", 403), which affects 25 of the 37
tests in that file. That failure predates this change and is not caused by the timeline code.
