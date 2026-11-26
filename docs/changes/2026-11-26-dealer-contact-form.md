# Dealer contacts: dealer profile in the add/edit form (backend)

Cross-app note. Web side: [ogera_crm_web/docs/changes/2026-11-26-dealer-contact-form.md](../../../ogera_crm_web/docs/changes/2026-11-26-dealer-contact-form.md)

## What changed

- `models/dealer_profile.py`: `DealerProfile` gets a `contact` relationship (back_populates `dealer_profile`).
- `models/contact.py`: `Contact.dealer_profile` one-to-one relationship added.
- `schemas/dealer_profile.py` (new): `DealerProfileBase`, `DealerProfileUpdate`, `DealerProfileResponse`. Answers are a free `data` dict, same as partner and consumer profiles.
- `schemas/contact.py`: `dealer_profile` added to `ContactCreate`, `ContactUpdate` and `ContactResponse`.
- `schemas/__init__.py`: exports the dealer profile schemas.
- `routes/contacts.py`:
  - `_check_type_profiles` rejects `dealer_profile` unless the contact type is `dealer`.
  - `_validate_dealer_profile_data` validates against `contact_field_definitions` with `entity_type='dealer'`.
  - Create attaches the dealer profile and validates it, including on an empty payload, so required dealer fields are enforced.
  - Update merges the sent keys into the stored `data`, then validates the merged result, so a partial update still sees the answers already saved.

## Access / data impact

- No new permissions. Create and update use the same role checks as other contact types.
- Writes to the existing `dealer_profiles` table (created by migration 001). No new columns or migrations.
- The typed columns on `dealer_profiles` (business_name, gstin, town_city, pin_code, dealer_type, assigned_rep, credit_limit, outstanding_balance) are not written by this change. Dealer answers go into `data` only.
- Dealer field definitions are admin-managed in Settings; none are seeded by this change.

## Verification

- `tests/test_contacts_module.py`: 33 failed, 8 passed, both before and after this change (checked against a clean HEAD worktree). The failures are existing tests that create contacts without `lead_source_id`, which the schema has required for some time. Not caused by this change, not fixed here.
- Temporary API smoke test (deleted after the run), against the in-memory SQLite test app:
  - create without required dealer answers → 422
  - create with valid answers → 201, `dealer_profile.data` returned
  - GET returns the stored answers
  - PUT with a partial payload → merged result returned
  - PUT with an invalid dropdown value → 422
  - `dealer_profile` on a consumer contact → 400
  - partner contact → `dealer_profile: null`
- Not run against the live PostgreSQL database. Per CLAUDE.md, nothing was migrated or written there. Before relying on this, confirm `dealer_profiles` exists in the tenant schema.

## Known issues or not done yet

- The typed dealer columns (GSTIN, credit limit, etc.) are not in the form. Add them later if the business needs them.
- Consumer and partner dynamic fields (beyond the hard-coded ones) are still not sent in the save payload. Pre-existing, not changed here.
