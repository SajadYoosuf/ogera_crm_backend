# 2026-11-27 — Dashboard query batching and auth JOIN

## What changed

### 1. Merged Employee + Role fetch in `get_current_user` (`auth.py`)

Previously two sequential queries: `SELECT * FROM employees WHERE id=?` then
`SELECT * FROM roles WHERE id=?`. Replaced with one LEFT OUTER JOIN query.
Saves one round trip on **every authenticated request** (~400 ms on the
Vercel → Hostinger path).

### 2. Batched follow-up counts into one query (`services/dashboard_service.py`)

`_followups` ran 4 separate `SELECT COUNT(interactions.id) WHERE ...` calls
(overdue, today, upcoming, due_total). Replaced with a single query using
PostgreSQL aggregate filters:

```sql
SELECT
  COUNT(id) FILTER (WHERE due_at < :now …)      AS overdue,
  COUNT(id) FILTER (WHERE due_at BETWEEN …)     AS today,
  COUNT(id) FILTER (WHERE due_at > :tomorrow …) AS upcoming,
  COUNT(id) FILTER (WHERE due_at <= :tomorrow …) AS due_total
FROM interactions WHERE kind = 'task' …
```

Saves 3 round trips (~1.2 s).

### 3. Batched lead counts into one query (`services/dashboard_service.py`)

`_leads` ran 4–5 separate `SELECT COUNT(leads.id) WHERE ...` calls (open,
new_in_period, cohort_converted, uncontacted, converted_in_period for head
view). Replaced with one conditional-aggregate query over `*live` base filter.
Employee-specific converted_in_period still uses a separate RecordLog query
(different table).

Saves 3–4 round trips (~1.2–1.6 s).

### 4. Merged needs_action count + rows into one query (`services/dashboard_service.py`)

`_contacts` ran `SELECT COUNT(*)` and `SELECT … LIMIT 8` against the same
subquery. Replaced with a window function: `COUNT(*) OVER ()` returns the total
while the LIMIT still caps the rows fetched.

Saves 1 round trip (~400 ms).

### 5. Merged three Interaction GROUP BY queries in `_team` (`services/dashboard_service.py`)

`_team` ran `activities`, `calls`, and `completed` as three separate GROUP BY
queries against Interactions (all sharing the same WHERE conditions). Replaced
with a single query using three conditional aggregates grouped by `done_by`.

Saves 2 round trips (~800 ms).

### 6. Merged WhatsApp count queries (`services/dashboard_service.py`)

`_whatsapp_messages` ran `unread_conversations` and `sent_in_period` as two
separate COUNT queries. Replaced with a single query using two aggregate
filters on `WhatsAppMessage`.

Saves 1 round trip (~400 ms).

## Access / data impact

No schema changes. No data changes. All changes are read-only query rewrites.
The aggregate FILTER syntax requires PostgreSQL 9.4+; Hostinger PostgreSQL
satisfies this.

## Verification

- `python -c "import main"` passes.
- `test_auth.py` + `test_dashboard.py`: 25/25 passed.
- `test_whatsapp_inbox.py` run alone: 19/19 passed.
- Other changed-area files pass when run in isolation; failures in the full
  suite are pre-existing test-order pollution (SQLite in-memory state bleed),
  not caused by this change.
- Not run against the live Vercel/Hostinger environment.

## Expected impact

| View | Queries before | Queries after (est.) | Time before | Time after (est.) |
|---|---|---|---|---|
| Non-head (sales/tele) | ~35 | ~24 | ~14 s | ~9–10 s |
| Head (admin/manager) | ~50 | ~37 | ~20 s | ~15 s |

Estimates assume ~400 ms per round trip on the Vercel → Hostinger path
(measured from logs: 16 s / 37 local queries ≈ 430 ms/query on the server).

## Known issues / not done

- The 15-second frontend timeout (`REQUEST_TIMEOUT_MS`) may still be tight for
  the head/admin dashboard. If it still times out, raise it to 25 s or batch
  `_recent` and `_unassigned` similarly.
- `_team` still runs 2 more grouped queries (overdue on Interaction by
  `assigned_to`, converted on RecordLog). These are on different columns/tables
  and are harder to merge without correctness risk.
- Composite indexes (`CREATE INDEX CONCURRENTLY …`) have not been confirmed
  applied to the live database. They remain needed.
- `FIREBASE_SERVICE_ACCOUNT_JSON` on Vercel still needs updating to inline JSON.

Change note for the previous performance commit: `docs/changes/2026-11-27-performance-fixes.md`
