# API support for the mobile app

## Why
The Flutter app was written for the old Next.js API. To connect it to this backend, the app now adapts to the FastAPI shapes, and the backend gains the few reads the app has no other way to get.

## What changes
- `GET /contacts/map`: contacts that have a location, trimmed for the map (same filters as the list).
- `GET /contacts/stage-types`: the fixed pipeline stages in board order.
- `GET /contacts` filters: `awareness_mode` (1 = needs education, 0 = sales-ready, includes not assessed) and `unattributed` (no recorded creator). List rows also carry `awareness_mode`, `work_area`, `latitude`, `longitude` and `lead_source.slug`.
- `GET /employees/directory`: names, roles and phones of active staff, open to every signed-in employee (the other employee routes stay admin / manager only).
- `GET /performance?start_date&end_date`: leads vs awareness contacts added per employee. Team view for admin / manager, self view for everyone else.
- `POST` and `PUT /contacts` now fill `is_verified` and `missing_fields` in the response (they were always empty there).
- `GET /contacts/{id}/stages` breaks same-second ties by id, so the newest stage is first.
- `database.py`: `DATABASE_URL` overrides the MySQL settings, and `SQL_ECHO=0` silences SQL logging. SQLite works for tests (in memory) and the end-to-end run (file, one connection per session). MySQL behaviour is unchanged.
- The random assignee pick and the location upload's "ignore duplicates" insert use the SQLite equivalent when the database is SQLite.

## Tests
- `tests/test_mobile_api.py`: login, contacts (list, map, filters, stages), directory, performance, follow-up tasks, staff locations and sales permissions, through the real app.
- `tests/e2e_server.py` runs the API on a seeded throwaway SQLite database; `tests/run_mobile_e2e.py` starts it and runs the Flutter app's end-to-end test against it.
- `requirements-dev.txt` adds `pytest` and `httpx`.

## Out of scope
Stage management endpoints, removing a follow-up, unassigning a contact. Sales and products have their own change notes.

## Verification
`python -m pytest tests` in the venv, and `python tests/run_mobile_e2e.py` (also needs Flutter).
