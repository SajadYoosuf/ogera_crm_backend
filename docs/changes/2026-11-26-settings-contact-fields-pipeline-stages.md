# Settings: contact field definitions create + admin list endpoints

## What changed

**`routes/contact_fields.py`** — rewritten with two new endpoints:
- `POST /contact-field-definitions` (admin only) — create a new field definition; validates entity_type, enforces unique `field_name` within entity_type, auto-assigns sort_order as max+1
- `GET /contact-field-definitions/all` (admin only) — returns all rows including inactive; the existing `GET /contact-field-definitions` only returns active rows, so the settings page could not see or reactivate hidden fields

Also extended entity_type validation from `consumer|partner` to `consumer|partner|dealer` on both the per-entity GET and the new POST.

**`schemas/contact_field_definition.py`** — added `ContactFieldDefinitionCreate` schema (entity_type, field_name, field_label, field_type, options, is_required, sort_order, show_in_list) and `VALID_ENTITY_TYPES` constant.

**`schemas/__init__.py`** — exported `ContactFieldDefinitionCreate`.

## Access / data impact

- No schema migrations needed; inserts go into the existing `contact_field_definitions` table.
- New `POST` and admin `GET /all` require `admin` role; existing endpoints unchanged.
- `entity_type=dealer` now accepted on `GET /{entity_type}` — previously returned 400.

## Verification

- `python -c "import routes.contact_fields; import schemas.contact_field_definition; print('OK')"` — passes
- pytest not run (no test suite for these routes yet)

## Known issues or not done yet

- No pytest tests for the new POST endpoint or the `/all` admin GET
- Web change note: `docs/changes/2026-11-26-settings-contact-fields-pipeline-stages.md` in `ogera_crm_web/`
