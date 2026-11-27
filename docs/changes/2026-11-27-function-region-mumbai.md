# 2026-11-27 — Run the backend functions in Mumbai (`bom1`)

## What changed

New `vercel.json` sets `"regions": ["bom1"]`. It overrides the Function Region in the Vercel project settings.
Until now the functions ran in `iad1` (US East).

Why: every request opens a new database connection and sends 7–47 statements one after another, so the distance
between the function and the database is paid many times. Server times in production were about 2.6–3.8 s for
ordinary requests and 12.8 s for the dashboard.

## Access / data impact

No code, schema, data or role change. The region applies to production when this is deployed.

## Verification

- The file parses as JSON and is not ignored by git. Vercel's documentation confirms the `regions` property and
  that the Hobby plan allows any single region.
- `uv lock --check`, `import main` and `py_compile` pass; no conflict markers.
- Full pytest suite on SQLite: `157 failed, 410 passed, 2 xfailed`, the same counts as before this change.
- Not run: `uv sync --frozen`, and no deployment, so neither the new region nor any speed gain is verified.

## Known issues / not done

- Database location: Mumbai, India (server `srv1763319`), as read from Hostinger hPanel on 2026-11-27. If the
  database moves, change the region with it.
- After deploying, check that new log entries run in `bom1` and compare the time of `GET /branches` (about 2.7 s
  before).
- To roll back, set the region to `iad1` or remove `vercel.json`, then redeploy.
- Connection reuse, fewer statements per request and startup time are unchanged.
