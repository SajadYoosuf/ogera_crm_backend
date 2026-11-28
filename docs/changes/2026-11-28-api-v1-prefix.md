# Add /api/v1 prefix to all routes

**Date:** 2026-11-28

## What changed

- `main.py`: All `app.include_router(...)` calls now pass `prefix="/api/v1"`, moving every endpoint from e.g. `/auth/login` to `/api/v1/auth/login`.
- `main.py`: OpenAPI docs, `openapi.json`, and ReDoc moved to `/api/v1/docs`, `/api/v1/openapi.json`, `/api/v1/redoc`.
- `/` (root status) and `/health` remain unprefixed.
- `/uploads` static mount remains unprefixed.

**Companion change:** `ogera_crm_web/docs/changes/2026-11-28-api-v1-prefix.md`

## Access / data impact

No schema or data changes. All authenticated and public endpoints now require the `/api/v1` path segment.

External integrations (Zapier, Google Sheets, website forms) that call `/leads/inbound/...` or `/webhooks/...` must be updated to `/api/v1/leads/inbound/...` and `/api/v1/webhooks/...`.

## Verification

- Reviewed all 28 `include_router` calls updated in `main.py`.
- FastAPI server not started in this session; test by running `uvicorn main:app --reload` and hitting `/api/v1/docs`.

## Known issues / not done yet

- Mobile app API base URL not updated here — needs a separate update if it hardcodes paths.
- No backward-compat redirect from old paths to new `/api/v1` paths.
