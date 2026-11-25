# Auto-assign excludes admin and manager

## What changed
- `services/lead_service.py` `auto_assign()`: the candidate query now joins `roles` and filters out employees whose role slug is `admin` or `manager`. The least-loaded rule and the lowest-id tie-break are unchanged.
- `docs/modules/leads.md` section 6: documents the exclusion.

## Access / data impact
- Leads created with no assignee (admin/manager create without `assigned_to`, unassigned imports set to "auto", inbound channels such as website, Sheets and WhatsApp) are no longer assigned to an admin or manager.
- Admin and manager can still assign a lead to themselves manually.
- Existing lead assignments are not changed.

## Verification
- Ran `auto_assign` against an in-memory SQLite database with one admin (0 open leads, id 1) and one sales employee (0 open leads, id 2). It returned 2.
- Not run: the full API against PostgreSQL, and no pytest suite exists in this repo yet.

## Known issues or not done yet
- `auto_assign` still does not check the `leads` read permission (the bulk-assign endpoint does). An active employee without lead permission could still be picked.
- Ties still always go to the lowest id, so with equal workloads the same employee gets the next lead.
