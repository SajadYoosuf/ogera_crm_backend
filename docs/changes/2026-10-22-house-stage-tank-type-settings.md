# House stage and tank type move to Settings

## Why
House stage and tank type were fixed lists in code, each with its own endpoint (`/contacts/house-stages`, `/contacts/tank-types`) that no client called. They are business-specific values an admin should manage, like partner types. Written after the code (the plan was agreed in chat first).

## What changed
- `settings_catalog.py`: new categories `house_stage` and `tank_type`, seeded with the old values. Settings shows how many consumers use each value.
- `routes/contacts.py`: `_check_consumer_options` rejects a value not in Settings (422) on create and update. A hidden value stays allowed on the record that already has it. The two list endpoints are removed.
- `schemas/consumer_profile.py`: fields are plain text (max 50); blank text clears the field.
- `schemas/enums.py`, `schemas/__init__.py`: `HouseStage`, `TankType` and the unused `HouseStageEnum`, `TankTypeEnum` removed.
- `contact_import.py`: sheet text is matched to house stages by value or label, with the old prefix rules ("Under Construction - …" → under_construction).
- `rule.md` rule 6 and `API.md` updated.

## Out of scope
- Custom fields per tenant (step 2 of the design; waits for multi-tenancy).
- Cleaning old free-text house stages already in the database.

## Verification
- `tests/test_contacts_module.py`: values must be in Settings, admin-added tank type accepted and counted, hidden stage kept on old records, blank clears.
- Full backend suite: 226 passed on the working tree; 249 passed on the committed snapshot.

Same commit: web (`ogera_crm_web/docs/changes/2026-10-22-house-stage-tank-type-settings.md`) and mobile.
