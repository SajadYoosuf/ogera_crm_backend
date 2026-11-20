# 2026-11-21 — Leads module (backend)

## Summary
Lead Core tasks, lead status table, lead import and export, and fixes found while testing the Leads page in the browser.

## Database
Run once per tenant schema (already applied to `ventino`):

- `scripts/migrate_leads_branch.sql` — adds `leads.branch_id` (FK to `branches`, ON DELETE SET NULL) and index.
- `scripts/migrate_leads_phone_unique.sql` — normalises `leads.phone_key` and adds a partial unique index on live leads. Check the duplicate report first.
- `scripts/migrate_lead_statuses_table.sql` — creates `lead_statuses`, seeds 9 statuses, remaps old values (`qualified` → `pre_qualified`, `lost` → `lost_lead`).

Tool script, not a migration — do not run casually:
- `scripts/clear_lead_data.sql` — permanently deletes all leads for one tenant. Unlinks converted contacts first.

## Endpoints
- `GET /leads/statuses` — active `lead_statuses` rows, in order. `converted` is not a row; it is set only by convert.
- `PUT /leads/{id}` — `status` is validated against active statuses (or `converted`). Returns 400 for unknown values.
- `GET /leads/import/template` — CSV template.
- `POST /leads/import/analyze` — dry run; writes nothing. Returns columns, field mapping, summary and problem rows.
- `POST /leads/import/commit` — writes ready and warning rows. `assign_mode`: `auto`, `unassigned` or `specific` (with `assigned_to`).
- `GET /leads/export?format=csv|xlsx` — same filters as the list.
- `GET /leads` — `date_from` / `date_to` are IST calendar days (see fixes).

## Import behaviour (`lead_import.py`)
- Only phone is required. Name, address, notes, tags, source and created date are optional.
- Column detection by header alias. Meta Ads headers (`created_time`, `ad_name`, `full_name`, `phone_number`, etc.) are recognised.
- Phone cells may hold a field label (`p:+974…`, `Mob: +971…`) and several numbers. The first valid number becomes the phone; others go to notes as "Other number".
- A bare 11–15 digit number with no `+` is treated as international (e.g. `96556692452`, Kuwait).
- Created date keeps only the calendar date of ISO timestamps (`2026-08-19T06:58:29-05:00` → 19 Aug 2026).
- Duplicates (same phone as an existing lead or contact, or repeated in the file) are skipped.
- A missing name is stored as an empty string (the live column is NOT NULL).
- Leads get `created_by` and `branch_id` from the importing employee.

## Export (`lead_export.py`)
CSV and Excel only. Reuses the generic writers in `contact_export.py`.

## Fixes in this change
- `create_lead` / `update_lead` returned leads whose `creator`/`branch` relations were loaded lazily after the session closed (500 `DetachedInstanceError`). Both relations are now loaded before the session closes.
- `lead_import` crashed on a `lead_sources` row with NULL name or slug. Empty labels are skipped.
- Date filter compared IST dates against UTC timestamps, so "Today" missed leads created between 00:00 and 05:30 IST. The filter now uses IST day bounds (`business_time.ist_day_start_utc`).
- WhatsApp template sending is off by default. Set `WHATSAPP_TEMPLATE_SENDING_ENABLED=1` to turn it on. Both send functions check the flag.

## Known gaps
- Imported dates and manual backdating: imports accept any date, while manual creation allows backdating only for admin and manager.
- Admin accounts are hidden from the employee list, so leads assigned to them show as unassigned in the UI.
- The WhatsApp button in the lead modal shows "Sent" even when sending is off (frontend).
