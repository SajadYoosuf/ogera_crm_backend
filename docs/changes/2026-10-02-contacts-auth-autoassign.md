# Contacts: login required, auto-assign, offline GPS, referrer validation

## What changes
- `routes/contacts.py`: every `/contacts/*` route requires a token. `created_by` is set from the token.
- `assigned_to` is optional on create: a random active employee is picked, excluding `admin`, `manager` and `support` (409 if none).
- `address` is mandatory on create (`district` already was). Legacy rows without an address still read fine.
- `latitude` / `longitude` (new columns): required for the Offline lead source, rejected for any other source.
- `referred_by_id` and `lead_source_id` are validated on create/update; a contact cannot refer itself.
- `scripts/migrate_contact_location.sql` (new) and `create_tables.sql`: add the two columns. Run the migration on existing databases.
- `API.md` (one folder up): contacts section rewritten.

## Out of scope
- Unassigned contacts (`assigned_to` stays NOT NULL), duplicate phone check, role rules on contacts, other old web form fields.

## Verification
- In-memory SQLite smoke test: auto-assign picks an eligible role, offline GPS rules, missing address 422, bad/good referrer. Not run against MySQL.
- `POST /contacts` now creates the first lead stage (`new_lead`, or `aware` when `awareness_mode` is 1), so new contacts are no longer stage-less.
