# Partner profession type enum

## Why
Partner type was free text with no way to set it when creating a contact. Partners need a fixed list: plumber, engineer, interior designer, architect, contractor, electrician.

## What changes
- `schemas/enums.py`: new `ProfessionType` enum (adds `other` for legacy imports, hidden from pickers).
- `schemas/partner_profile.py`: `profession_type` is the enum; responses map legacy text ("Interior Designer", "Plumber") to a member, unknown text to `other`.
- `ContactCreate` / `ContactUpdate` accept `partner_profile` (partner contacts only); update upserts it.
- `GET /contacts/profession-types` lists the options. `GET /contacts?profession_type=` takes the enum and matches legacy rows case-insensitively.
- `scripts/import_professionals.py` writes lowercase values.
- Column stays VARCHAR, so no migration; existing rows keep their text and are normalised on read.

## Out of scope
Rewriting existing rows; the mobile app.

## Verification
Schema checks in the venv (valid, invalid and legacy values) and a TestClient call to the new endpoint.
