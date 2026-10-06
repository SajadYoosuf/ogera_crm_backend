# Offline location: needed only when the contact is created

## Why
Imported contacts have the Offline source but no GPS, and they should not count as unverified for that. A location is only needed at the moment an offline contact is created; after that only the address matters.

## What changes
- `verification.py`: an offline lead no longer needs `location` to be verified (`missing_fields` and `verified_clause` both updated). `created_by` is still required for offline leads.
- `routes/contacts.py`: `POST /contacts` still requires `latitude` and `longitude` for the Offline source. `PUT /contacts/{id}` no longer requires them; they are still rejected for non-offline sources.
- `API.md` updated.

## Out of scope
Backfilling coordinates for imported contacts; the mobile app's GPS capture.

## Verification
Direct calls: offline create without coordinates is rejected; offline update without coordinates is accepted; an offline contact without coordinates has no `location` in `missing_fields`.
