# Contacts Module — Backend

## What this module does

The Contacts module manages every person or business tracked in the CRM — consumers (home owners, buyers) and partners (installers, professionals, dealers). It handles listing, filtering, creating, editing, importing from CSV/Excel, exporting to Excel, bulk-assigning to sales staff, and viewing stats.

> **Note on "dealer" type:** An older version had a `dealer` contact type as a third enum value. It was removed in commit `7516458`. The `ContactType` enum now only has `consumer` and `partner`. If you see `contact_type = 'dealer'` rows in the database they are legacy data — `scripts/remove_dealer_stages.py` can clean up leftover pipeline stages.

---

## Data Model

### `contacts` table (core)

| Column | Type | Notes |
|---|---|---|
| `id` | BigInteger | Primary key |
| `name` | String | Optional at create (verified later) |
| `phone` | String | Required, unique per tenant |
| `district` | String | Optional |
| `address` | String | Optional |
| `contact_type` | Enum | `consumer` / `partner` |
| `lead_source_id` | FK → `lead_sources` | Where the contact came from |
| `assigned_to` | FK → `employees` | Sales person; NULL = unassigned |
| `is_verified` | Boolean | Verification gate flag |
| `deleted_at` | DateTime | Soft delete |

### `consumer_profiles` table (JSONB)

Each consumer contact has one row here. The `data` column is a free-form JSON dict. Keys come from the tenant's `contact_field_definitions` — there are **no fixed columns** like `house_stage`.

```
consumer_profiles
  id
  contact_id  (FK → contacts)
  data        (JSONB)  e.g. {"house_stage": "planning", "plot_size": 1200}
```

### `partner_profiles` table (JSONB)

Same pattern for partner contacts.

```
partner_profiles
  id
  contact_id  (FK → contacts)
  data        (JSONB)  e.g. {"trade": "mason", "work_area": "kozhikode"}
```

### `contact_field_definitions` table

Defines what profile fields exist for a tenant. The importer, exporter, and filter menus all read this table — no hardcoded field names anywhere.

| Column | Type | Notes |
|---|---|---|
| `field_name` | String | Key used in JSONB data |
| `field_label` | String | Human-readable label |
| `field_type` | Enum | `text` / `dropdown` / `number` / `boolean` |
| `entity_type` | Enum | `consumer` / `partner` |
| `options` | JSON array | For dropdown fields — list of valid values |
| `is_active` | Boolean | Inactive fields are hidden |
| `sort_order` | Integer | Display order |

---

## API Endpoints

All endpoints require a valid JWT (`Authorization: Bearer <token>`). The tenant schema is derived from the logged-in employee.

**Route order rule:** Fixed-path routes (`/stats`, `/bulk-assign`, `/import/analyze`, etc.) must be declared **before** `/{contact_id}` in `routes/contacts.py`. FastAPI matches routes top-to-bottom; if `/{contact_id}` comes first, the string "stats" matches it as an integer param and returns a 422 error.

### GET `/contacts`

List contacts with filtering, sorting, and pagination.

**Query params:**

| Param | Type | Description |
|---|---|---|
| `page` | int | Page number (default 1) |
| `page_size` | int | Items per page (default 20, max 100) |
| `search` | string | Search name or phone |
| `contact_type` | string | `consumer` / `partner` |
| `lead_source_id` | int | Filter by source |
| `assigned_to` | int | Filter by assigned employee |
| `is_verified` | bool | Filter verified/unverified |
| `district` | string | Filter by district |
| `sort_by` | string | Column to sort on |
| `sort_dir` | string | `asc` / `desc` |

**Returns:** `{ items: Contact[], total: int, page: int, page_size: int }`

---

### GET `/contacts/{id}`

Get a single contact with their consumer or partner profile data.

**Returns:** Full contact object with nested profile `data` dict.

---

### POST `/contacts`

Create a new contact. Only `phone` and `lead_source_id` are required at creation; name, district, and address are filled in later during the verification step.

**Body:**
```json
{
  "phone": "9876543210",
  "lead_source_id": 3,
  "contact_type": "consumer",
  "name": "optional",
  "district": "optional"
}
```

---

### PUT `/contacts/{id}`

Update a contact's core fields or profile data.

**Body:** Any subset of contact fields. Profile fields go inside `profile_data: { field_name: value }`.

---

### DELETE `/contacts/{id}`

Soft-delete a contact (sets `deleted_at`, does not remove the row).

---

### GET `/contacts/stats`

Returns the count of unassigned contacts, broken down by lead source. Used to populate the Bulk Assign modal.

**Returns:**
```json
{
  "total_unassigned": 142,
  "unassigned_by_source": {
    "Facebook": 60,
    "Walk-in": 42,
    "Referral": 40
  }
}
```

---

### POST `/contacts/bulk-assign`

Assign a batch of unassigned contacts to employees. Each rule picks the N oldest unassigned contacts from one source and assigns them to one employee.

**Body:**
```json
{
  "assignments": [
    { "source_id": 2, "assigned_to": 5, "count": 20 },
    { "source_id": 2, "assigned_to": 7, "count": 15 }
  ]
}
```

**Rules:**
- `source_id: null` pulls from any source
- Max 500 contacts per rule, max 50 rules per request
- Only picks contacts where `assigned_to IS NULL` and `deleted_at IS NULL`

**Returns:** `{ "assigned": 35 }`

---

### GET `/contacts/export`

Export contacts to an Excel file. Applies the same filters as `GET /contacts`.

**Query params:** Same as `GET /contacts` list params.

**Returns:** `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` file download.

Profile fields in the export come from `contact_field_definitions`, so the columns are tenant-specific.

---

### GET `/contacts/import/template`

Download a blank CSV template showing all importable columns. The template includes core fields (name, phone, district, etc.) plus all active profile fields from `contact_field_definitions`.

**Returns:** `text/csv` file download.

---

### POST `/contacts/import/analyze`

Dry-run an import file (CSV or Excel). Validates every row but writes nothing to the database.

**Body:** `multipart/form-data` with one file field (`file`).

**Returns:**
```json
{
  "columns": [
    { "index": 0, "header": "Name", "field": "name", "sample": "Ahmed Ali" }
  ],
  "fields": {
    "name": "Name", "phone": "Phone", "house_stage": "House Stage"
  },
  "missing_required": ["phone"],
  "summary": { "total": 200, "ready": 185, "warning": 10, "duplicate": 3, "error": 2 },
  "rows": [
    { "row": 5, "status": "warning", "issues": ["district: unknown value"], "name": "John", "phone": "9876" }
  ]
}
```

The `columns` array powers the column-mapping step in the frontend modal. The `fields` dict populates the "Ogera CRM field" dropdown for each column.

---

### POST `/contacts/import/commit`

Execute an import that was previewed with `/analyze`. Re-validates the file and writes new contacts to the database.

**Body:** `multipart/form-data` with:
- `file` — the CSV/Excel file
- `assigned_to` — JSON array of employee IDs to distribute assignments among (optional)

**What it creates per row:**
1. `Contact` with core fields
2. `ConsumerProfile(data=...)` or `PartnerProfile(data=...)` with JSONB profile fields
3. `LeadStage` set to `new_lead`
4. `FollowupSchedule` due in 24 hours

---

### GET `/contacts/partner-types`

Returns the list of valid partner type values. Reads from `contact_field_definitions` for the `trade`/partner fields.

---

### GET `/contacts/type-counts`

Returns the count of contacts per `contact_type`.

**Returns:** `{ "consumer": 450, "partner": 120 }`

---

## Import Pipeline — How It Works

The importer is fully data-driven. Adding a new field to `contact_field_definitions` makes it automatically available in imports — no code changes needed.

```
1. load_field_defs(db)
   └─ Reads all active ContactFieldDefinition rows, grouped by entity_type

2. detect_all_columns(rows, field_defs)
   └─ Reads the header row
   └─ Maps core columns: "Phone" → "phone", "Name" → "name", etc.
   └─ Maps profile columns: "House Stage" → "house_stage" (from field defs)

3. For each data row:
   └─ Maps raw values to core fields (name, phone, district, etc.)
   └─ For profile fields: looks up ContactFieldDefinition, calls _validate_profile_field()
   └─ Validation by field_type:
        dropdown → checks options list
        number   → parses int/float
        boolean  → yes/no/true/false
        text     → truncates to 500 chars
   └─ Stores result in row["profile_data"] dict

4. run_import() (commit only):
   └─ profile_data = row.pop("profile_data", {})
   └─ contact.consumer_profile = ConsumerProfile(data=profile_data)
```

---

## Export Pipeline — How It Works

`contact_export.py` reads `contact_field_definitions` via `get_export_config(db)` to know which columns to include in the Excel file. It then reads `c.consumer_profile.data` or `c.partner_profile.data` to get the JSONB values for each row.

---

## Business Rules

- **Phone is unique per tenant.** Duplicate phones during import are flagged as `duplicate` status.
- **Soft deletes.** Contacts are never hard-deleted. Filtered out using `deleted_at IS NULL`.
- **Verification gate.** Contacts created from leads may have no name; `is_verified = False` until a sales rep confirms the details.
- **Assignment.** `assigned_to = NULL` means unassigned. Bulk assign only touches unassigned contacts.
- **Multi-tenant.** Every query uses `get_session_for_schema(schema)` with the tenant's PostgreSQL schema. Never query without the schema context.

---

## Key Files

| File | Purpose |
|---|---|
| `routes/contacts.py` | All HTTP route handlers |
| `contact_import.py` | CSV/Excel import validation and commit logic |
| `contact_export.py` | Excel export generation |
| `models/contact.py` | SQLAlchemy Contact model |
| `models/consumer_profile.py` | ConsumerProfile model (JSONB data column) |
| `models/partner_profile.py` | PartnerProfile model (JSONB data column) |
| `models/contact_field_definition.py` | Per-tenant dynamic field definitions |
| `schemas/contact.py` | Pydantic request/response schemas |
