# Contacts type counts follow the list's ownership scope

Date: 2026-11-28

## What changed

- `routes/contacts.py` → `contact_type_counts` (`GET /contacts/type-counts`) now applies the same ownership scope as `GET /contacts` (`contact_query`):
  - Admins and managers (`is_head`) see company-wide counts, as before.
  - Other staff only count contacts where `assigned_to` or `created_by` is them.
- Before this change, the tab badges counted every contact while the list only showed the user's own contacts. A non-admin user could see "All 51" with an empty list and a "No matches found" message.

## Access / data impact

- Role access: non-head staff now see smaller tab counts (only their own contacts). Admins and managers see no change.
- No schema, migration or data change. Read-only query.

## Verification

- `python -m py_compile routes/contacts.py` passes.
- Not run: no backend test suite exists, and the endpoint was not exercised against a live database. Verify by logging in as a non-admin user and comparing `/contacts/type-counts` with the `total` returned by `/contacts`.

## Known issues or not done yet

- The web empty state still says "No matches found" when nothing is filtered. It would be clearer as "No contacts assigned to you yet" when no filters or search are active. Not changed here.
- Web counterpart: no frontend change was needed; `ContactsView.tsx` already reads these counts.
- Not committed.
