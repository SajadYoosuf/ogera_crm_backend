# Dashboard summary API (backend)

Web side: `ogera_crm_web/docs/changes/2026-11-26-role-dashboard.md`.

## What changed
- `routes/dashboard.py` (new): `GET /dashboard/summary?period=today|7d|30d|custom&date_from=&date_to=&assigned_to=`,
  registered in `routes/__init__.py` and `main.py`.
- `services/dashboard_service.py` (new): follow-ups and work queue (leads and contacts), leads (status, source,
  trend, not contacted), activities, contact pipelines (consumer, partner, dealer), recent updates, team table,
  unassigned and handoff lists, and WhatsApp counts.
- `tests/test_dashboard.py` (new).

Definitions:
- Open lead: not converted, lost, junk or not qualified.
- Not contacted: an open lead with no logged activity. A status change or a WhatsApp message does not count.
- Overdue and due today: the Tasks page rules. A task late today counts in both.
- Conversion rate: of the leads created in the period, the share now converted.
- A staff member's conversions are those they converted (`record_logs`), even if the lead was reassigned.

## Access / data impact
- Read only. No schema change, no migration.
- Admin and manager get everyone's numbers, or one employee's with `assigned_to`. Every other role gets only
  their own; `assigned_to` is ignored for them. The view is chosen from the role on the server.
- "Their own" means leads and contacts assigned to them. Unlike the contacts list, a contact they only created
  is not counted.
- WhatsApp connection and template status are returned to admin only.
- If a tenant lacks the WhatsApp tables, those two sections are `null` and the rest still loads.

## Verification
- `pytest tests/test_dashboard.py` on in-memory SQLite: 21 passed (repeated several times).
- Neighbouring files, run alone: `test_whatsapp_inbox.py` 19 passed; `test_lead_tasks.py`,
  `test_leads_mvp.py` and `test_contacts_module.py` still have failures (13, 1 and 33). These files failed
  before this change too.
- Not run against PostgreSQL or any live database. Not timed on a large tenant.

## Known issues or not done yet
- One request builds every section with a few dozen queries. If it is slow on real data, split it or cache it.
- The earlier `GET /leads/stats` and contact count endpoints are still not limited by role; the dashboard does
  not use them.
- Contact creation, edits and assignment are not written to `record_logs` by their routes, so recent updates
  for contacts are incomplete.
- Auto-assignment still counts lost and junk leads as open (it uses its own list).
