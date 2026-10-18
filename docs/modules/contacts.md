# Module: Contacts

One contact API for consumers, partners and dealers. `contact_type` picks the rules and the details block.

Status: the rules in this guide are in the working tree. The migration for existing databases is `scripts/migrate_contacts_module.sql`. Not committed yet at the time of writing.

## Files

| File | What it does |
| --- | --- |
| `routes/contacts.py` | Every `/contacts` route, the create and update rules, the visibility checks |
| `contact_access.py` | Who may see which contacts (`visible_to`, `get_visible_contact`) |
| `phone_utils.py` | `clean_phone` (validation) and `phone_key` (duplicate key) |
| `request_context.py` | `client_platform`: reads `X-Client: mobile` |
| `schemas/contact.py` | Request and response shapes, phone cleaning |
| `schemas/consumer_profile.py`, `schemas/partner_profile.py`, `schemas/dealer_profile.py` | The three details blocks |
| `schemas/enums.py` | Fixed lists: `HouseStage`, `TankType`, `ProfessionType`, `ContactType`, `StageEnum` |
| `models/contact.py`, `models/consumer_profile.py`, `models/partner_profile.py`, `models/dealer_profile.py` | Tables |
| `verification.py` | "Verified" rule (Python and SQL) |
| `contact_import.py`, `contact_export.py` | Spreadsheet import and export |
| `routes/performance.py` | Lead and awareness counts (awareness = reached the Aware stage) |

## Routes

| Method and path | Who may call | Result |
| --- | --- | --- |
| `GET /contacts` | Signed in; visibility applies | Paged list with stage, owner, verification, `house_stage` and `owner_name` |
| `GET /contacts/{id}` | Signed in; visibility applies | One contact with its profiles, stage and missing fields |
| `POST /contacts` | Signed in | Creates a contact and its details block |
| `PUT /contacts/{id}` | Signed in; visibility applies | Partial update |
| `DELETE /contacts/{id}` | Signed in; visibility applies | Soft delete |
| `PUT /contacts/{id}/consumer-profile`, `DELETE /contacts/{id}/consumer-profile` | Signed in; visibility applies | Consumer details |
| `GET /contacts/{id}/stages`, `POST /contacts/{id}/stages` | Signed in; visibility applies | Stage history and moves |
| `GET /contacts/map` | Signed in; visibility applies | Contacts with a location |
| `GET /contacts/type-counts` | Signed in; visibility applies | Counts per type, including `dealer` |
| `GET /contacts/house-stages`, `GET /contacts/tank-types`, `GET /contacts/profession-types` | Signed in | The fixed lists |
| `GET /contacts/export` | Signed in; visibility applies | CSV, Excel or PDF with the same filters as the list |
| `GET /contacts/import/template`, `POST /contacts/import/analyze`, `POST /contacts/import/commit` | Admin, manager | Spreadsheet import |

## Business logic

### Create (`POST /contacts`)

1. **Always required:** name, phone and lead source. A phone is cleaned (spaces, dashes, brackets and dots removed) and must be 7–15 digits with an optional `+`.
2. **Mobile only** (`X-Client: mobile`): address and district are required. The web form may omit them.
3. **Details block for the type:** a partner needs `partner_profile` with a profession; a dealer needs `dealer_profile` with an owner name; a consumer's `consumer_profile` is optional. A block is only accepted on its own type.
4. **Phone is free:** no live contact (any type) may hold the same last-10-digits key. A deleted contact frees its number. The `409` names the other contact only for admins and managers.
5. **Lead source and location:** an offline source needs latitude and longitude. Other sources must not send them.
6. **Owner:** `assigned_to` if given, otherwise a random active employee who takes leads (not admin, manager or support).
7. The contact starts at the New Lead stage and is saved with its details in one transaction.

### Update (`PUT /contacts/{id}`)

The same type and location rules apply to the fields sent. Address and district cannot be cleared from the mobile app. A changed phone must be free, and its key is recalculated. A details block is created if missing and updated otherwise.

### Verified

A contact is verified when it has name, phone, district, address and lead source, and also: a consumer has a house stage, a dealer has an owner name, and an offline lead has a creator. Otherwise the response lists `missing_fields`. The SQL twin used for filters lives beside the Python rule in `verification.py`.

### Visibility

- admin and manager: every contact.
- team_lead, sales_executive, tele_caller, field_executive, support: contacts where they are the **creator** (`created_by`) or the **assignee** (`assigned_to`).
- Anything outside that set is `404`, on every contact route, the lists, the map, the counts, the exports and the contact's activities and stages.
- Creators keep access after reassignment.

## Database

```
contacts                          one row per contact, any type
--------
id, contact_type [consumer|partner|dealer]
name                              account name for dealers
phone, phone_key (last 10 digits, indexed, duplicate check)
phone_alt
district  VARCHAR(80) NULL        optional on web; mobile requires it
address   VARCHAR(255) NULL
latitude, longitude               offline leads
lead_source_id  FK lead_sources   required
assigned_to  FK employees         who is working the lead now
created_by   FK employees         who entered it
referred_by_id  FK contacts       pending (see below)
created_at, updated_at, deleted_at

consumer_profiles (1:1)           house_stage [ready|under_construction|renovation]
--------------------              current_tank_type [plastic|concrete|steel|other]
                                  current_tank_capacity (litres), current_tank_age (years)
                                  (awareness_mode removed)

partner_profiles (1:1)            profession_type [plumber|engineer|contractor|interior_designer|architect|electrician|other]
------------------                work_area, sample_given, projects_per_year
                                  (awareness_mode removed)

dealer_profiles (1:1) NEW         owner_name (required), email (optional)
-------------------

lead_stages                       the pipeline; "aware" is awareness
```

Fixed lists live in `schemas/enums.py`, not in Settings. The `enum_values` rows for the old partner-type category are left in the database, unused.

## End-to-end flow

```
Create a dealer from the mobile app
  app ──POST /contacts  X-Client: mobile  {contact_type:"dealer", name, phone,
        lead_source_id, address, district, dealer_profile:{owner_name, email}}──▶ routes/contacts.py
    ▶ schemas/contact.py: phone cleaned and validated, lead source required
    ▶ client_platform = mobile: address and district present
    ▶ _check_type_profiles: dealer_profile only on a dealer
    ▶ _ensure_phone_free: phone_key not used by a live contact (any type)
    ▶ _pick_random_assignee (when assigned_to is absent)
    ▶ INSERT contacts (phone_key set) + dealer_profiles + lead_stages(new_lead)
    ◀── 201 ContactResponse (is_verified, missing_fields)

Staff opens the list
  app ──GET /contacts──▶ contact_query(visible_to(user)) ──▶ rows
    ◀── only contacts the user created or is assigned to (admin and manager: all)
```

## Pending (not built yet, by decision)

- **Verification of each contact's details** beyond the current rule. Stage moves are gated by it today.
- **Referred by** pointing at existing consumer, partner, dealer or sale contacts. `referred_by_id` stays as it is.
- **Connecting to existing consumer, partner, dealer or sale contacts** (for example a dealer linked to the sales it made).
- **Dealer pipeline.** Dealers currently use the consumer pipeline and auto-assignment.
- **Teams.** Team lead visibility (their team's contacts) needs a team model.
- **Tank type and house stage in Settings.** Both are code lists by decision; revisit if the business wants them editable.

## Known gaps

1. **Stage changes take `changed_by` from the request body.** It should be the signed-in user. Pre-existing; not changed in this module.
2. **The web and mobile apps** need the new fields (`dealer_profile`, `owner_name` on lists, fixed lists instead of Settings for house stage, tank type and partner type) and the `X-Client: mobile` header in the mobile app.
3. **Phone duplicates already in the data** (2 numbers at the time of planning) are reported by the migration, not merged.

## Tests

`tests/test_contacts_module.py` (type matrix, duplicate phone across types, mobile-only fields, fixed lists, visibility), `tests/test_mobile_api.py`, `tests/test_partner_stages.py`, `tests/test_performance.py`, `tests/test_settings.py`.
