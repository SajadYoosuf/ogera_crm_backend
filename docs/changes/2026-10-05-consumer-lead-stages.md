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
