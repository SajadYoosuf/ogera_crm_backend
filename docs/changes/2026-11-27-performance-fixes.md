# 2026-11-27 — Performance and stability fixes

## What changed

### 1. Halved DB connections per request (`auth.py`, `database.py`)
`get_current_user` was a plain function that opened a schema session, read the employee and
role, then closed the session and returned. `get_tenant_db` opened a **second** session for
the route's business logic — meaning every authenticated request opened two TCP connections
to Hostinger PostgreSQL.

`get_current_user` is now a generator dependency. It opens one session, runs auth queries,
attaches `_db` to the employee, then `yield`s the employee — the session stays open for the
whole request lifetime. `get_tenant_db` now simply yields `current_user._db` (zero new
connections). `get_session_for_schema` stores the schema name in `db.info["_tenant_schema"]`
so other services can read it without an extra round-trip.

Files: `auth.py`, `database.py`

### 2. Removed redundant `with get_session_for_schema` in route handlers
Every route handler that opened its own schema session (`with get_session_for_schema(schema) as db:`)
even though `get_current_user` already opened one now uses `current_user._db` directly —
the already-open, search_path-set session. Saves one full Hostinger round-trip per request
(TCP + TLS + PostgreSQL auth + `SET search_path`).

Affected files:
- `routes/leads.py` — 14 handlers
- `routes/contacts.py` — 2 handlers
- `routes/whatsapp_inbox.py` — 4 handlers
- `routes/lead_whatsapp.py` — 2 handlers
- `routes/lead_statuses.py` — 4 handlers

The unused `get_session_for_schema` import was removed from each of these files.

### 3. Firebase `os.path.exists` guard (`firebase_init.py`)
Vercel logs showed 184 errors/day: `Firebase Admin SDK init failed: [Errno 2] No such file or
directory: 'C:/secrets/ogera-crm-firebase-adminsdk-fbsvc-d812d71693.json'`. The env var
`FIREBASE_SERVICE_ACCOUNT_JSON` was set to a Windows file path; on Linux that file doesn't
exist. Added an `os.path.exists()` check before attempting to open the file. When the path
doesn't exist, logs a clear warning that Option B (inline JSON) must be used in cloud
deployments.

**To fix FCM on Vercel:** update the `FIREBASE_SERVICE_ACCOUNT_JSON` env var to the full JSON
contents of the service account file (not the file path).

### 4. FCM schema-less session bug (`services/notification_service.py`)
`dispatch_notification_push` used `SessionLocal()` with no `SET search_path`, so queries for
`employees` and `fcm_devices` hit `public.employees` — which doesn't exist. 94 errors/day.

Fixed by:
- Having `create_notification` read `db.info["_tenant_schema"]` (stored by `get_session_for_schema`)
  and pass it as a new `tenant_schema` parameter to the background task.
- `dispatch_notification_push` now uses `get_session_for_schema(tenant_schema)` for both the
  lookup and the token cleanup. If `tenant_schema` is None, logs a warning and returns
  (graceful degradation, not a crash). If Firebase is not initialised, it returns before any
  database lookup.

### 5. Missing composite indexes (`models/interaction.py`, `models/lead_stage.py`,
`models/record_log.py`)
Added indexes for the query patterns the dashboard and inactivity rules run:
- `idx_interactions_kind_done_by_at(kind, done_by, logged_at)` — activity logs by employee
- `idx_interactions_kind_assigned_due(kind, assigned_to, due_at)` — open tasks by employee
- `idx_lead_stage_log_contact_changed(contact_id, changed_at, id)` — `ROW_NUMBER()` window fn
- `idx_record_logs_action_actor(entity_type, action, actor_id, created_at)` — conversion stats

**These indexes require a migration to take effect on the live database.** Run once against
each tenant schema: `CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_... ON interactions (...);`
etc. The SQLAlchemy model declarations do not auto-migrate existing databases.

## Access / data impact
No schema or data changes in this commit. The index definitions in models will only apply to
new databases created from scratch; live tenant schemas need the `CREATE INDEX CONCURRENTLY`
commands applied manually or via a migration script.

## Verification
- Test suite, `DATABASE_URL=sqlite:///:memory:`:
  - Unmodified HEAD: `343 failed, 221 passed, 2 xfailed`.
  - This change: `354–355 failed, 209–210 passed` across runs (the count varies by run order).
  - Changed-area files (leads, lead tasks, lead access, notifications, WhatsApp inbox, contacts,
    dashboard, auth), run together: `91 failed, 98 passed` here against `92 failed, 97 passed`
    at HEAD. No failure is new in these files, and `test_daily_mode_respects_max_days` now passes.
  - The 91 remaining failures also fail at HEAD; they depend on test order.
- Regression found and fixed during this work: sending FCM from a background thread that opened a
  database session made lead saves fail under the test database's shared connection. Firebase
  readiness is now checked before any database lookup, which also avoids the lookup when Firebase
  is not configured.
- Not run: against the live Vercel environment or the live Hostinger database.
- Firebase error-log finding: Vercel logs (184 errors/day, Windows file path).

## Known issues / not done
- Full-suite failures: about 340 tests fail on unmodified HEAD and about 12 more fail only in
  full-suite order, though they pass when their files run alone. Not yet traced.
- `routes/notifications.py` is unchanged: `_recent_for` takes a schema string and opens its own
  session in a worker thread, so it doesn't use the request session.
- Dashboard query merging (reducing ~35 sequential COUNT queries to ~5–8) was not done in this
  commit due to risk. The connection-reuse fix alone saves 1–3 round-trips per request and
  should substantially reduce dashboard latency.
- `FIREBASE_SERVICE_ACCOUNT_JSON` Vercel env var still needs manual update to the inline JSON
  string for FCM to actually work.
- Composite indexes require a manual `CREATE INDEX CONCURRENTLY` run against each tenant
  schema (see SQL below); they will not apply automatically.
