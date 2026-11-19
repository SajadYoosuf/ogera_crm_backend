# Leads Module — Backend API Reference

**Base URL prefix:** `/leads`  
**Auth:** Every endpoint requires `Authorization: Bearer <jwt>` except the inbound endpoints (see §2).  
**Tenant routing:** The JWT carries a `tenant` claim (the PostgreSQL schema name, e.g. `ventino`). The backend opens that schema for every query — leads from one tenant are never visible to another.

---

## What is a Lead?

A lead is a **pre-contact record** — a person who has shown interest but has not yet become a full Contact. Leads live in the per-tenant `leads` table and flow through these statuses:

```
new → contacted → qualified → converted
                            ↘ lost
```

When a lead is converted, a Contact row is created (or an existing one is linked) and the lead's `converted_at` and `contact_id` are filled.

---

## 1. Authenticated Endpoints (JWT required)

### 1.1 List leads

```
GET /leads
```

Returns a paginated list of leads for the caller's tenant, newest first.

**Query parameters**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `skip` | int ≥ 0 | no (default 0) | Rows to skip (for pagination) |
| `limit` | int 1–100 | no (default 20) | Rows per page |
| `status` | string | no | `new` / `contacted` / `qualified` / `converted` / `lost` |
| `source_id` | int | no | Filter by source (see Source IDs below) |
| `assigned_to` | int | no | Filter by employee ID |
| `search` | string | no | Fuzzy match on name, phone, or email |
| `date_from` | YYYY-MM-DD | no | Created on or after this date |
| `date_to` | YYYY-MM-DD | no | Created on or before this date |

**Success — `200 OK`**
```json
{
  "items": [
    {
      "id": 1,
      "first_name": "Ravi",
      "last_name": "Kumar",
      "phone": "9876543210",
      "email": null,
      "source_id": 3,
      "source_name": "Meta Ads",
      "status": "new",
      "score": 55,
      "tags": ["hot"],
      "whatsapp_sent": true,
      "assigned_to": 7,
      "assigned_name": "Mohammed K",
      "created_at": "2026-11-01T08:30:00",
      "updated_at": "2026-11-01T08:30:00"
    }
  ],
  "total": 142,
  "skip": 0,
  "limit": 20
}
```

---

### 1.2 Get one lead

```
GET /leads/{lead_id}
```

Returns the full lead record including `raw_payload`, `address`, `converted_at`, and `contact_id`.

**Success — `200 OK`**
```json
{
  "id": 1,
  "first_name": "Ravi",
  "last_name": "Kumar",
  "phone": "9876543210",
  "email": null,
  "source_id": 3,
  "source_name": "Meta Ads",
  "status": "new",
  "score": 55,
  "notes": "Meta Ads: Summer campaign",
  "address": "Calicut, Kerala",
  "tags": ["hot"],
  "raw_payload": { "ad_name": "Summer 2026", "form_name": "Lead form" },
  "whatsapp_sent": true,
  "whatsapp_sent_at": "2026-11-01T08:30:05",
  "assigned_to": 7,
  "created_by": null,
  "converted_at": null,
  "contact_id": null,
  "created_at": "2026-11-01T08:30:00",
  "updated_at": "2026-11-01T08:30:00"
}
```

**Errors**

| Status | When |
|---|---|
| `404` | Lead not found or soft-deleted |

---

### 1.3 Create a lead manually

```
POST /leads
```

Staff member adds a new lead by hand (e.g. a walk-in visitor or a phone enquiry).

**Request body**
```json
{
  "first_name": "Fathima",
  "last_name": "Nazar",
  "phone": "9000000001",
  "email": "fathima@example.com",
  "source_id": 1,
  "notes": "Came to showroom, interested in 1000L",
  "address": "Malappuram",
  "tags": ["hot", "1000L"],
  "assigned_to": 5,
  "created_at": "2026-10-15T00:00:00Z"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `first_name` | string (1–200 chars) | **yes** | |
| `last_name` | string | no | |
| `phone` | string | no | Normalised to last-10-digits internally |
| `email` | string | no | |
| `source_id` | int | no (default Walk-in) | See Source IDs |
| `notes` | string | no | |
| `address` | string | no | |
| `tags` | string[] | no | |
| `assigned_to` | int (employee id) | no | Skips auto-assign if set |
| `created_at` | ISO datetime | no | Override for importing historical leads |

**Query parameter**

| Parameter | Description |
|---|---|
| `allow_duplicate=true` | Admin/manager only — skip the duplicate phone/email check |

**Success — `201 Created`** — returns the full `LeadResponse` (same shape as §1.2).

**Errors**

| Status | When |
|---|---|
| `409` | Phone or email already exists in leads or contacts. Body includes `existing_id` and `existing_type` |
| `403` | Non-admin/manager tried to pass `allow_duplicate=true` |

**Business rules**
- Phone is normalised: `+91 98765-43210` and `9876543210` both map to the same `phone_key`.
- After creation, a WhatsApp welcome message is sent in the background (if phone is present).
- If `assigned_to` is not set, the system auto-assigns to the employee with the fewest open leads.
- `score` is computed automatically (0–100). See §4 for the scoring rules.

---

### 1.4 Update a lead

```
PUT /leads/{lead_id}
```

Partially update a lead. Only fields you send are changed — omitted fields are left as-is.

**Request body** (all fields optional)
```json
{
  "status": "contacted",
  "assigned_to": 7,
  "notes": "Called back, interested"
}
```

Updatable fields: `first_name`, `last_name`, `phone`, `email`, `status`, `source_id`, `notes`, `address`, `tags`, `assigned_to`.

**Success — `200 OK`** — returns the updated `LeadResponse`.

**Common use cases**
- Move a lead forward: `{ "status": "qualified" }`
- Reassign from the table row: `{ "assigned_to": 9 }`
- Inline status change in the UI table: sends a single-field PATCH

---

### 1.5 Delete a lead

```
DELETE /leads/{lead_id}
```

Soft-deletes the lead by setting `deleted_at`. The row is preserved for history. The lead disappears from all list responses.

**Success — `204 No Content`**

---

### 1.6 Convert a lead to Contact

```
POST /leads/{lead_id}/convert
```

Converts a qualified lead into a full Contact record.

**How it works**
1. Looks up the lead.
2. Checks if a Contact with the same phone_key already exists.
3. If yes → links the lead to that contact (`contact_id` is filled); `created: false`.
4. If no → creates a new Contact from the lead's name, phone, email, source; `created: true`.
5. Sets `lead.converted_at` and `lead.status = "converted"`.

**Success — `200 OK`**
```json
{
  "lead_id": 42,
  "contact_id": 189,
  "created": true
}
```

**After conversion:** navigate the user to `/contacts/{contact_id}`.

**Errors**

| Status | When |
|---|---|
| `404` | Lead not found |
| `409` | Lead already converted |

---

### 1.7 Manually send WhatsApp message

```
POST /leads/{lead_id}/send-whatsapp
```

Triggers the WhatsApp welcome template for a lead. Useful when the automatic send on creation failed, or after the phone number was corrected.

**Success — `200 OK`**
```json
{ "success": true }
```
or
```json
{ "success": false, "detail": "Lead has no phone number" }
```

---

### 1.8 Get lead stats

```
GET /leads/stats
```

Returns lead counts grouped by status and source. Used by the dashboard widget and the Bulk Assign modal.

**Success — `200 OK`**
```json
{
  "total": 142,
  "by_status": {
    "new": 80,
    "contacted": 35,
    "qualified": 15,
    "converted": 10,
    "lost": 2
  },
  "by_source": {
    "Meta Ads": 70,
    "Walk-in": 40,
    "WhatsApp": 20,
    "Referral": 12
  },
  "unassigned_by_source": {
    "Meta Ads": 30,
    "Walk-in": 10
  }
}
```

---

### 1.9 Bulk assign leads

```
POST /leads/bulk-assign
```

Assign batches of unassigned leads to employees. Used by the Bulk Assign modal in the UI.

**How it works:** For each rule, the service picks the N oldest unassigned (`assigned_to IS NULL`) leads from that source, ordered by `created_at ASC`, and sets their `assigned_to`.

**Request body**
```json
{
  "assignments": [
    { "source_id": 3, "assigned_to": 5, "count": 15 },
    { "source_id": 3, "assigned_to": 7, "count": 15 },
    { "source_id": null, "assigned_to": 9, "count": 10 }
  ]
}
```

| Field | Type | Notes |
|---|---|---|
| `source_id` | int or null | null = any source |
| `assigned_to` | int | Employee ID |
| `count` | int (1–500) | Max leads to assign in this rule |

**Success — `200 OK`**
```json
{ "assigned": 38 }
```

> `assigned` may be less than the total requested if fewer unassigned leads exist for a source.

---

## 2. Public Inbound Endpoints (API key — no JWT)

These endpoints are designed to be called by **external scripts** (website forms, Zapier, Google Apps Script) that do not have an employee login.

### Authentication

Pass a tenant API key in the `X-Api-Key` header:
```
X-Api-Key: your_raw_api_key_here
```

The backend hashes the key with **SHA-256** and checks it against `public.tenant_api_keys`. If the hash matches an active key for the tenant (identified by the `{tenant_slug}` in the URL), the request proceeds.

> Keys are generated by an admin in CRM Settings → API Keys.

### Why SHA-256 instead of bcrypt?

API keys are long, high-entropy random strings — not passwords humans choose. There is no dictionary of "likely" API keys to try. SHA-256 is sub-millisecond and appropriate here; bcrypt would add ~200ms latency per inbound lead with no real security benefit.

---

### 2.1 Website form

```
POST /leads/inbound/website/{tenant_slug}
```

Create a lead from a website contact form submission.

**Headers**
```
X-Api-Key: your_key
Content-Type: application/json
```

**Request body**
```json
{
  "first_name": "Ravi",
  "last_name": "Kumar",
  "phone": "9876543210",
  "email": "ravi@example.com",
  "notes": "Interested in 2000L tank"
}
```

Extra fields (UTM parameters, page URL, form name, etc.) are **accepted and stored** in `raw_payload` — nothing is lost.

```json
{
  "first_name": "Ravi",
  "phone": "9876543210",
  "utm_source": "google",
  "page_url": "https://example.com/contact"
}
```

**Success — `201 Created`**
```json
{ "id": 851, "status": "created" }
```

**Source ID assigned:** `4` (Website) — always.

**Browser fetch example**
```js
fetch('https://api.yourdomain.com/leads/inbound/website/ventino', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'X-Api-Key': 'your_key'
  },
  body: JSON.stringify({ first_name: 'Ravi', phone: '9876543210' })
});
```

---

### 2.2 Google Sheet / Meta Ads (via Zapier or Apps Script)

```
POST /leads/inbound/sheet/{tenant_slug}
```

Create a lead from a Meta Ads Google Sheet row. Maps Meta Ads fields (`ad_name`, `form_name`, etc.) to the lead.

**Request body**
```json
{
  "first_name": "Ahmed",
  "phone": "9123456789",
  "ad_name": "Summer 2026 — Water Tank",
  "form_name": "Lead form",
  "campaign_id": "120201234567890",
  "ad_set_id": "120209876543210"
}
```

| Field | Required | Notes |
|---|---|---|
| `first_name` | **yes** | |
| `phone` | recommended | Stored + used for WhatsApp |
| `ad_name` | no | Auto-fills notes if `notes` is blank |
| `form_name` | no | Stored in `raw_payload` |
| `campaign_id` | no | Stored in `raw_payload` |
| `ad_set_id` | no | Stored in `raw_payload` |

**Source ID assigned:** `3` (Meta Ads) — always.

**Zapier setup**
1. Trigger: Google Sheets → New Spreadsheet Row
2. Action: Webhooks by Zapier → POST
   - URL: `https://api.yourdomain.com/leads/inbound/sheet/ventino`
   - Headers: `X-Api-Key: your_key`
   - Payload: map sheet columns → `first_name`, `phone`, `ad_name`, `form_name`

**Google Apps Script setup**
```js
function onEdit(e) {
  var row = e.range.getRow();
  var sheet = e.source.getActiveSheet();
  var payload = {
    first_name: sheet.getRange(row, 1).getValue(),
    phone:      sheet.getRange(row, 2).getValue(),
    ad_name:    sheet.getRange(row, 3).getValue()
  };
  UrlFetchApp.fetch(
    'https://api.yourdomain.com/leads/inbound/sheet/ventino?api_key=YOUR_KEY',
    { method: 'post', contentType: 'application/json', payload: JSON.stringify(payload) }
  );
}
```

---

### Inbound error responses

| Status | When |
|---|---|
| `401` | Missing, invalid, or revoked API key; unknown tenant slug |
| `422` | Validation error (e.g. `first_name` missing) |

---

## 3. Source IDs

| ID | Name | Notes |
|---|---|---|
| 1 | Walk-in | Staff-entered from a showroom visit |
| 2 | Referral | Referred by an existing customer |
| 3 | Meta Ads | Facebook / Instagram paid ads (via Sheet) |
| 4 | Website | Website contact form |
| 5 | WhatsApp | Inbound WhatsApp message |
| 6 | JustDial | JustDial enquiry |
| 7 | GMB | Google Maps / Google Business Profile |
| 8 | Field Visit | Staff visited the prospect on-site |

---

## 4. Lead Scoring (0–100)

The score is computed by `services/lead_service.py → compute_score()` on every create and update. It is stored on the row.

| Rule | Points |
|---|---|
| Source = Meta Ads (3) | 30 |
| Source = Website (4) | 25 |
| Source = WhatsApp (5) | 20 |
| Source = Referral (2) | 20 |
| Source = GMB (7) | 20 |
| Source = JustDial (6) | 15 |
| Source = Field Visit (8) | 15 |
| Source = Walk-in (1) | 10 |
| Has phone | +10 |
| Has email | +10 |
| Has last name | +5 |
| WhatsApp sent | +10 |
| Status = qualified | +5 |
| **Maximum** | **100** |

---

## 5. Duplicate Detection

Before creating any lead, `check_duplicate()` runs:
1. Normalises phone → `phone_key` (last 10 digits).
2. Queries `leads` for matching `phone_key` where `deleted_at IS NULL`.
3. Queries `contacts` for matching `phone_key`.
4. Checks email similarly if provided.

On a duplicate, the endpoint returns `409` with:
```json
{
  "detail": "duplicate",
  "existing_id": 42,
  "existing_type": "lead"
}
```

The frontend can offer to open the existing record or override with `?allow_duplicate=true` (admin/manager only).

---

## 6. Auto-Assignment

When a lead is created without `assigned_to`, `auto_assign()` picks the active employee with the **fewest open leads** (`status NOT IN (converted, lost)`). This ensures the workload is spread evenly across the sales team.

---

## 7. WhatsApp Auto-Reply

After every lead creation (inbound or manual), if the lead has a phone number, a background task calls `services/whatsapp_service.py → send_whatsapp_template()` with the `welcome_lead` template. On success, `whatsapp_sent = True` and `whatsapp_sent_at` are recorded. Failures are logged but do **not** fail the lead creation.

---

## 8. Backend File Map

| File | Purpose |
|---|---|
| `routes/leads.py` | Authenticated CRUD + stats + bulk-assign + convert |
| `routes/lead_inbound.py` | Public inbound endpoints (website + sheet) |
| `services/lead_service.py` | All business logic: scoring, dedup, auto-assign, convert |
| `services/whatsapp_service.py` | WhatsApp template sender |
| `services/lead_access.py` | `is_head()` helper (admin/manager check) |
| `schemas/lead.py` | Pydantic request and response models |
| `models/lead.py` | SQLAlchemy Lead model |
| `models/tenant_api_key.py` | TenantApiKey model (public schema) |

---

## 9. Multi-Tenant Schema — How Auto-Insertion Works

This section explains, in plain terms, how leads from an external source (website, Google Sheet, Zapier) land in the **right company's database** without any employee logging in.

### 9.1 What is "multi-tenant"?

The CRM can serve multiple companies at once. Each company (tenant) is completely separated from others — their leads, contacts, and employees never mix.

The separation happens at the **PostgreSQL schema level**. Think of a schema like a folder inside one database:

```
PostgreSQL database: ogera_crm
├── public             ← shared tables (tenants list, API keys)
│   ├── tenants        ← one row per company
│   └── tenant_api_keys← API keys for inbound endpoints
│
├── ventino            ← Ventino's private tables
│   ├── leads
│   ├── contacts
│   └── employees
│
└── acme_corp          ← Acme Corp's private tables (completely separate)
    ├── leads
    ├── contacts
    └── employees
```

When a lead arrives for Ventino, the backend runs:
```sql
SET search_path TO ventino;
INSERT INTO leads (...) VALUES (...);
```

This means the row goes into `ventino.leads`, never into `acme_corp.leads`.

---

### 9.2 The `tenant_slug` — the key that picks the right schema

Every inbound URL contains a `{tenant_slug}`:

```
POST /leads/inbound/website/ventino
                            ↑
                   this is the slug
```

The slug is exactly the `schema_name` stored in `public.tenants`. So when you call `.../ventino`, the backend looks up the tenant whose `schema_name = 'ventino'` and writes the lead into the `ventino` schema.

**The admin finds their slug** in CRM Settings → API Keys. It is set when the tenant account is created and never changes.

---

### 9.3 API Key — how the inbound endpoint knows you are allowed

External scripts (a website, a Zapier zap, an Apps Script) have no employee login. They prove their identity with an **API key** instead.

**How an API key is created:**
1. An admin opens CRM Settings → API Keys → Generate Key.
2. The backend generates a long random string (e.g. `ak_3f8...d9`).
3. It hashes the key with SHA-256 and stores **only the hash** in `public.tenant_api_keys`. The raw key is shown once and never stored.
4. The admin copies the raw key into their script.

**How the backend checks it on each inbound request:**

```
Request arrives: POST /leads/inbound/website/ventino
                 Header: X-Api-Key: ak_3f8...d9

Step 1  Look up tenant by slug
        SELECT * FROM public.tenants WHERE schema_name = 'ventino'
        → found: tenant_id = 1

Step 2  Hash the key from the header
        sha256("ak_3f8...d9") → "e3b0c4..."

Step 3  Check the hash against stored keys
        SELECT * FROM public.tenant_api_keys
        WHERE tenant_id = 1
          AND key_hash = 'e3b0c4...'
          AND is_active = true
        → found: key is valid ✓

Step 4  Open the tenant's schema and insert the lead
        SET search_path TO ventino;
        INSERT INTO leads (...) VALUES (...);
```

If the slug is wrong or the key hash doesn't match → `401 Unauthorized`. The error message is the same either way (`"Invalid tenant or API key"`) — this prevents an attacker from learning whether a slug exists.

---

### 9.4 Flow 1 — Website Contact Form

**Use case:** A visitor fills in a contact form on `example.com`. The form submission creates a lead in the CRM automatically.

```
Visitor fills form on example.com
        │
        ▼
Browser sends:
  POST https://api.yourdomain.com/leads/inbound/website/ventino
  Header: X-Api-Key: ak_3f8...d9
  Body: { first_name: "Ravi", phone: "9876543210" }
        │
        ▼
Backend (lead_inbound.py):
  1. Verify API key → opens ventino schema
  2. Create lead row in ventino.leads
     source_id = 4  (Website)
     raw_payload = { source: "website_form", ...any extra fields }
  3. Auto-assign to least-loaded employee
  4. Trigger WhatsApp welcome message (background task)
        │
        ▼
Response: { "id": 851, "status": "created" }
```

**What you need to set up:**

1. Get your `tenant_slug` and API key from CRM Settings → API Keys.
2. Add this to your website's form submission handler:

```js
// Plain JavaScript — works with any website (HTML, WordPress, etc.)
document.querySelector('#contact-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const data = new FormData(e.target);
  await fetch('https://api.yourdomain.com/leads/inbound/website/ventino', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Api-Key': 'YOUR_API_KEY_HERE'
    },
    body: JSON.stringify({
      first_name: data.get('name'),
      phone:      data.get('phone'),
      email:      data.get('email'),
      notes:      data.get('message')
    })
  });
  // show thank-you message
});
```

**Extra fields:** Anything extra you send (UTM source, page URL, referrer) is stored in `raw_payload`. The backend never rejects unknown fields.

---

### 9.5 Flow 2 — Meta Ads via Google Sheet + Zapier

**Use case:** Meta Ads (Facebook/Instagram) collects lead forms. Meta pushes these to a Google Sheet automatically. Zapier watches the sheet and forwards each new row to the CRM.

```
Meta Ads campaign runs
        │
        ▼ (Meta auto-fills this)
Google Sheet — new row added:
  | Ahmed | 9123456789 | Summer 2026 — Water Tank | Lead form |
        │
        ▼ (Zapier watches the sheet)
Zapier trigger fires: "New Spreadsheet Row"
        │
        ▼
Zapier action: Webhooks → POST
  URL:    https://api.yourdomain.com/leads/inbound/sheet/ventino
  Header: X-Api-Key: ak_3f8...d9
  Body:   {
            first_name: "Ahmed",
            phone:      "9123456789",
            ad_name:    "Summer 2026 — Water Tank",
            form_name:  "Lead form"
          }
        │
        ▼
Backend (lead_inbound.py):
  1. Verify API key → opens ventino schema
  2. If notes is blank, auto-fills: "Meta Ads: Summer 2026 — Water Tank"
  3. Create lead row in ventino.leads
     source_id = 3  (Meta Ads)
     raw_payload = { ad_name, form_name, campaign_id, ... }
  4. Auto-assign + WhatsApp welcome (same as website flow)
        │
        ▼
Response: { "id": 852, "status": "created" }
```

**Zapier setup (step by step):**

1. **Trigger:** Google Sheets → "New Spreadsheet Row"  
   - Connect your Google account  
   - Select the spreadsheet Meta fills (the one from Meta Ads → Leads Center → Download)

2. **Action:** Webhooks by Zapier → "POST"  
   - URL: `https://api.yourdomain.com/leads/inbound/sheet/ventino`  
   - Payload type: `JSON`  
   - Headers: add `X-Api-Key` = your API key  
   - Data — map your sheet columns:

   | Zapier field | Sheet column | Notes |
   |---|---|---|
   | `first_name` | Full Name (or First Name) | Required |
   | `phone` | Phone Number | Recommended |
   | `email` | Email | Optional |
   | `ad_name` | Ad Name | Optional — goes into notes |
   | `form_name` | Form Name | Optional — stored in raw_payload |
   | `campaign_id` | Campaign ID | Optional |

3. Test the Zap — check the CRM leads list for the new row.

---

### 9.6 Flow 3 — Meta Ads via Google Sheet + Apps Script (no Zapier)

**Use case:** Same goal as §9.5, but using a free Google Apps Script instead of a paid Zapier account.

```
Meta Ads → Google Sheet → Apps Script (runs on each edit)
        │
        ▼
Apps Script reads the new row and POSTs to the CRM
```

**How to set it up:**

1. Open your Google Sheet.
2. Click **Extensions → Apps Script**.
3. Paste this code (edit the URL and API key):

```js
const CRM_URL = 'https://api.yourdomain.com/leads/inbound/sheet/ventino';
const API_KEY = 'YOUR_API_KEY_HERE';

// Column positions in your sheet (1-based)
const COL_NAME     = 1;   // A
const COL_PHONE    = 2;   // B
const COL_EMAIL    = 3;   // C
const COL_AD_NAME  = 4;   // D
const COL_SENT     = 5;   // E  ← we write "sent" here to avoid duplicates

function onEdit(e) {
  var sheet = e.source.getActiveSheet();
  var row   = e.range.getRow();

  // Skip the header row and rows already sent
  if (row <= 1) return;
  if (sheet.getRange(row, COL_SENT).getValue() === 'sent') return;

  var firstName = sheet.getRange(row, COL_NAME).getValue();
  if (!firstName) return;  // skip empty rows

  var payload = {
    first_name: String(firstName),
    phone:      String(sheet.getRange(row, COL_PHONE).getValue() || ''),
    email:      String(sheet.getRange(row, COL_EMAIL).getValue() || ''),
    ad_name:    String(sheet.getRange(row, COL_AD_NAME).getValue() || '')
  };

  var options = {
    method:      'post',
    contentType: 'application/json',
    headers:     { 'X-Api-Key': API_KEY },
    payload:     JSON.stringify(payload),
    muteHttpExceptions: true
  };

  var response = UrlFetchApp.fetch(CRM_URL, options);

  if (response.getResponseCode() === 201) {
    // Mark the row so we don't send it again on the next edit
    sheet.getRange(row, COL_SENT).setValue('sent');
  } else {
    Logger.log('CRM error: ' + response.getContentText());
  }
}
```

4. Save the script. Close the editor.
5. The script runs automatically whenever a new row is added or any cell is edited.

**The `sent` column trick:** Without it, every edit to the row (even fixing a typo) would create a duplicate lead. Writing `"sent"` in column E after a successful POST prevents the script from sending the same row again.

---

### 9.7 What happens after any inbound lead is created

No matter which flow was used (website, Zapier, or Apps Script), the backend runs the same steps after the INSERT:

```
Lead row inserted into ventino.leads
        │
        ├─ 1. Duplicate check (before insert)
        │      Phone already in leads?  → 409, return existing id
        │      Phone already in contacts? → 409, return existing id
        │
        ├─ 2. Score computed (0–100)
        │      source_id=3 (Meta Ads) → 30 pts
        │      has phone → +10 pts  ...etc
        │
        ├─ 3. Auto-assign
        │      Picks the active employee with the fewest open leads
        │      Sets lead.assigned_to
        │
        └─ 4. WhatsApp welcome (background task — does not block the response)
               Sends "welcome_lead" template to lead.phone via WhatsApp Business API
               On success: lead.whatsapp_sent = true, lead.whatsapp_sent_at = now()
               On failure: logs the error, lead creation is NOT rolled back
```

The API response (`{ id, status: "created" }`) is returned **before** step 4 completes — the WhatsApp send happens in the background so the form or script doesn't have to wait for it.

---

### 9.8 Summary Table

| Flow | Endpoint | Auth | Source ID | Who calls it |
|---|---|---|---|---|
| Website form | `POST /leads/inbound/website/{slug}` | API key | 4 (Website) | Browser JavaScript on your website |
| Meta Ads / Zapier | `POST /leads/inbound/sheet/{slug}` | API key | 3 (Meta Ads) | Zapier webhook action |
| Meta Ads / Apps Script | `POST /leads/inbound/sheet/{slug}` | API key | 3 (Meta Ads) | Google Apps Script in the Sheet |
| Manual (staff) | `POST /leads` | JWT (employee login) | Any | CRM web app / mobile app |
