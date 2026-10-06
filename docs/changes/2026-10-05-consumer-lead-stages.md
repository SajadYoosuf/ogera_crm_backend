# Consumer lead stages

## Why
The Sales/Lead Followup board needs the agreed consumer pipeline: New Lead, Aware, Interested, Quoted, Negotiating, Won, Lost, Repeat Customer. The backend stage list lacked `negotiating` and `repeat_customer`.

## What changes
- `schemas/enums.py` and `models/lead_stage.py`: add `negotiating` and `repeat_customer` to the stage enum. `first_call` and `referral` stay valid so existing history rows keep loading.
- `scripts/migrate_lead_stage_values.sql`: widens the `lead_stages.stage` ENUM (run once on existing databases).
- `scripts/seed.py`: stage types follow the new pipeline with the agreed descriptions.
- `API.md`: available stage values.

## Out of scope
Removing legacy values, order creation on Won, payment tracking, automatic re-engagement jobs.

## Verification
Import the app, POST a `negotiating` and a `repeat_customer` stage on a contact, confirm `lost` still requires `loss_reason`.

## Update 2026-10-06
- Added `connected` (and `first_call` is now a real stage, no longer legacy). Pipeline order: new_lead, first_call, connected, aware, interested, quoted, negotiating, won, lost, repeat_customer.
- New contacts always start as `new_lead` (Aware is reached by moving the lead there); `awareness_mode` no longer picks the first stage.
- `migrate_lead_stage_values.sql` now includes `connected`; applied to the live database on 2026-10-06.
- Data fixes run on the live database on 2026-10-06: `scripts/reassign_admin_leads.py` (531 admin-assigned contacts moved to sajad yoosuf, the only eligible employee) and `scripts/reset_aware_to_new_lead.py` (127 contacts moved from aware to new_lead; old rows stay in history).
- Stage gate: `POST /contacts/{id}/stages` returns `409` with `{ message, missing_fields }` when a lead leaves `new_lead` (to anything but `lost`) while `missing_fields(contact)` is not empty. The web shows a popup listing the missing details (`UnverifiedDialog`).
