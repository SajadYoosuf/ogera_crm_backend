# Contact verification status

## Why
Salespeople need to see at a glance which contacts are complete and which still have information pending.

## What changes
- New `verification.py` with `missing_fields(contact)`; a contact is verified only when nothing is missing.
- Required for every contact: name, phone, district, address, lead source.
- Offline (field visit) leads also need latitude, longitude and created_by.
- Consumers also need `consumer_profile.house_stage`; partners need `partner_profile.work_area`.
- `ContactListItem` and `ContactResponse` gain `is_verified` and `missing_fields`. Computed on read, no new column, no migration.
- `GET /contacts` also eager-loads `consumer_profile`.
- `API.md` documents the two new response fields.

## Added later
- `GET /contacts?verified=true|false`: `verified_clause()` in `verification.py` is the SQL twin of `missing_fields()`, so no stored column is needed. Keep the two in sync.
- Web: a Status dropdown in the filter bar.

## Out of scope
- Manual "mark as verified" override.
- Mobile app; dealer-specific fields (no dealer profile exists yet).

## Verification
Unit-check `missing_fields` for each contact type and the offline case; import the app and call the list route.
