# Tenant session commits reach the database before the response

**Date:** 2026-11-25
**Web counterpart:** none needed; the Tasks page refresh was already correct in tests.

## What changed

- `database.py`, `get_session_for_schema`: after `SET search_path`, the connection's transaction is now committed before the request's session is opened. Before, the session joined that transaction, so `db.commit()` in a route did not commit. The real commit happened only when the request ended, sometimes after the response had been sent.
- Symptom: an admin reassigns a follow-up; the response shows the new assignee, but a refresh straight away reads the old assignee, and a browser reload shows the right one.

## Access / data impact

No role, permission or schema change. Every write made through a tenant session (all tenant routes) is now committed by the time the route returns. No data is migrated.

## Verification

- Full backend suite: `210 failed, 310 passed, 2 xfailed, 14 errors`. The failing set is identical to the previous working-tree run; the one extra pass is `tests/test_reassign_persists.py`.
- `import main`: passes. `database.py` compiles.
- Not run: PostgreSQL. The SQLite test suite skips the `SET search_path` branch, so this path is only covered by the live check.
- Live check: not yet done. The reassign should read back correctly straight after the PATCH once the server reloads.

## Known issues or not done yet

- The live PostgreSQL check is pending.
- The 210 pre-existing failures and 14 errors remain (SQLite cannot create the `public` schema; reschedule-reason feature; several older lead-task and settings tests).
