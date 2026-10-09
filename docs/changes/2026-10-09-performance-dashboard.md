# Performance dashboard

## Why
Managers had one table of "who added how many contacts". They need to see who is falling behind (overdue follow-ups), where the pipeline stands and which lead sources convert; salespeople want their own numbers.

## What changes
- `routes/performance.py`: `GET /performance/summary`, `/team`, `/pipeline`, `/lead-sources` (see API.md). Aggregates are grouped in SQL, a fixed handful of queries per call.
- `tests/test_performance.py`: auth, 403 on team, bad range, empty period, self scoping, date filter, overdue sort, current-stage-only pipeline, source conversion.

## Out of scope
Mobile screen, per-person drill-down from the scorecard, CSV export, comparison with the previous period.

## Assumptions (differs from the original spec)
- The spec's `activity_log` and `follow_up_schedule` tables do not exist; both are `lead_activities` (`status` done / pending).
- The spec's roles `salesperson` / `manager` / `admin` map to: admin and manager see the team, every other role sees only their own.
- The pipeline shows the 10 real stages (incl. Aware, Interested, Repeat Customer), not the spec's Site Visit / On Hold.
- Route stays `/dashboard/performance`. The old `GET /performance` (leads vs awareness) is untouched.

## Verification
41 backend tests pass (9 new).
