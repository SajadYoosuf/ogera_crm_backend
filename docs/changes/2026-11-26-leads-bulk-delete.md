# Leads bulk delete (filter panel)

Change note for the web side: [../../../ogera_crm_web/docs/changes/2026-11-26-leads-bulk-delete.md](../../../ogera_crm_web/docs/changes/2026-11-26-leads-bulk-delete.md)

## What changed

- `POST /leads/bulk-delete` soft-deletes every live lead that matches the filters of `GET /leads`
  (status, source_id, assigned_to, date_from, date_to, search). Returns `{ "deleted": N }`.
- Filter logic moved from `list_leads` into `_apply_lead_filters()` in `services/lead_service.py`, so the list and the bulk delete use the same rules.
- `bulk_soft_delete_leads()` runs one `UPDATE ... SET deleted_at` for the matched ids and commits.
- New schemas in `schemas/lead.py`: `BulkDeleteLeadsRequest`, `BulkDeleteLeadsResponse`.
- Files: `routes/leads.py`, `services/lead_service.py`, `schemas/lead.py`, `tests/test_leads_bulk_delete.py`.

## Access / data impact

- Admin and manager only (`ensure_is_head`). Staff get 403.
- At least one filter is required. An empty body returns 400, so the whole list cannot be deleted by accident.
- Soft delete only (`deleted_at` is set). No rows are removed. Deleted leads are hidden from every list.
- Touches live data when used. No migration, no schema change.

## Verification

- `pytest tests/test_leads_bulk_delete.py`: 5 passed (staff 403, empty filter 400, filter scope, search count matches list total, date filter).
- `pytest tests/test_leads_mvp.py`: passes.
- `tests/test_lead_access.py`: 15 failures. These call `POST /contacts`, and `routes/contacts.py` is deleted in the current working tree (uncommitted), so they fail for reasons unrelated to this change.
- Not run against the live database. Not checked in a browser.

## Known issues or not done yet

- The working tree has uncommitted deletions of contact and feature routes, and `main.py` / `routes/__init__.py` are edited. Those are not part of this change and were not committed.
- No bulk delete for contacts yet (blocked on the contacts routes, see the contacts note when written).
- Update at commit time (2026-11-26): `routes/contacts.py` is present. The two points above and the
  `test_lead_access.py` reason describe the working tree at the time of writing. The route removals are
  committed separately (see the tableless-models note).
