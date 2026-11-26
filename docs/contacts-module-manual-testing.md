# Contacts Module — Manual Testing Guide

**Backend:** `http://localhost:8000`  
**Swagger UI:** `http://localhost:8000/docs`  
**Tenant:** `ventino`

---

## Setup

### 1. Start the backend
```
uvicorn main:app --reload
```

### 2. Get a token

**Admin / Manager** (can see all contacts, run all ops):
```
POST /auth/login
{ "email": "admin@ventino.com", "password": "..." }
```

**Staff** (sales_executive — limited visibility):
```
POST /auth/login
{ "email": "staff@ventino.com", "password": "..." }
```

Save the `access_token`. All requests below use it as:
```
Authorization: Bearer <token>
```

---

## 1. AUTH — Unauthenticated requests are rejected

| # | Request | Expected |
|---|---------|----------|
| 1.1 | `GET /contacts` (no token) | `401` |
| 1.2 | `POST /contacts` (no token) | `401` |
| 1.3 | `GET /pipeline/stages` (no token) | `401` |

---

## 2. PIPELINE STAGES — Verify seeds loaded correctly

### 2.1 Consumer stages
```
GET /pipeline/stages?contact_type=consumer
```
**Expected:** 200, list includes `new_lead`, `first_call`, `connected`, `aware`, `interested`, `quotation`, `negotiation`, `won`, `lost`, `after_sales_referring` (10 stages)

### 2.2 Partner stages
```
GET /pipeline/stages?contact_type=partner
```
**Expected:** 200, list includes `new_lead`, `first_call`, `connected`, `demo_introduction`, `aware`, `interested`, `active`, `referral`, `inactive` (9 stages)

### 2.3 Dealer stages
```
GET /pipeline/stages?contact_type=dealer
```
**Expected:** 200, list includes `prospect`, `application_received`, `verification`, `agreement_signed`, `active_dealer`, `inactive_dealer` (6 stages)

---

## 3. CONTACT CRUD

### 3.1 Create a consumer contact
```
POST /contacts
{
  "contact_type": "consumer",
  "name": "Ravi Kumar",
  "phone": "9876543210",
  "district": "Malappuram",
  "address": "MG Road, Tirur"
}
```
**Expected:** `200`, response includes `id`, `name`, `contact_type: "consumer"`  
**Save:** `consumer_id = <id>`

### 3.2 Create a partner contact
```
POST /contacts
{
  "contact_type": "partner",
  "name": "Suresh Plumber",
  "phone": "9876543211",
  "district": "Thrissur",
  "address": "Market St",
  "partner_profile": {
    "data": { "partner_type": "plumber", "work_area": "Thrissur" }
  }
}
```
**Expected:** `200`, `contact_type: "partner"`  
**Save:** `partner_id = <id>`

### 3.3 Create a dealer contact
```
POST /contacts
{
  "contact_type": "dealer",
  "name": "ABC Hardware Stores",
  "phone": "9876543212",
  "district": "Palakkad",
  "address": "Main Bazaar"
}
```
**Expected:** `200`, `contact_type: "dealer"`  
**Save:** `dealer_id = <id>`

### 3.4 Duplicate phone is rejected
```
POST /contacts
{
  "contact_type": "consumer",
  "name": "Duplicate Test",
  "phone": "9876543210",
  "district": "Kannur",
  "address": "X"
}
```
**Expected:** `409 Conflict`

### 3.5 Read a contact
```
GET /contacts/<consumer_id>
```
**Expected:** `200`, full contact object

### 3.6 Update a contact
```
PUT /contacts/<consumer_id>
{ "name": "Ravi Kumar Updated" }
```
**Expected:** `200`, `name` changed in response

### 3.7 Soft-delete a contact
```
DELETE /contacts/<consumer_id>
```
**Expected:** `204 No Content`

```
GET /contacts/<consumer_id>
```
**Expected:** `404` — deleted contact is gone

### 3.8 List contacts
```
GET /contacts?limit=10&sort=newest
```
**Expected:** `200`, `{ items: [...], total: N, skip: 0, limit: 10 }`

### 3.9 Type counts
```
GET /contacts/type-counts
```
**Expected:** `200`, `{ all: N, consumer: X, partner: Y, dealer: Z }`

---

## 4. PIPELINE — Stage moves on a contact

> Use `partner_id` from §3.2. First confirm it exists: `GET /contacts/<partner_id>`

### 4.1 Get current stage (should be empty)
```
GET /contacts/<partner_id>/stage
```
**Expected:** `200` with `stage_id: null` or `404` — contact has no stage yet

### 4.2 Set a stage
Get a valid stage id first:
```
GET /pipeline/stages?contact_type=partner
```
Pick the `id` for `first_call`.

```
POST /contacts/<partner_id>/stage
{ "stage_id": <first_call_stage_id> }
```
**Expected:** `200` or `201`

### 4.3 Confirm stage was saved
```
GET /contacts/<partner_id>/stage
```
**Expected:** `200`, `stage_slug: "first_call"`

### 4.4 Move to a later stage
```
POST /contacts/<partner_id>/stage
{ "stage_id": <interested_stage_id> }
```
**Expected:** `200`/`201`

### 4.5 Terminal stage requires loss_reason (partner: `inactive`)
```
POST /contacts/<partner_id>/stage
{ "stage_id": <inactive_stage_id> }
```
**Expected:** `422 Unprocessable Entity` — `loss_reason` is required

```
POST /contacts/<partner_id>/stage
{
  "stage_id": <inactive_stage_id>,
  "loss_reason": "not_interested",
  "loss_notes": "Partner said not interested at this time"
}
```
**Expected:** `200`/`201`

### 4.6 Stage type must match contact type
Get a consumer-only stage id (`won` from consumer pipeline). Try setting it on the partner contact:
```
POST /contacts/<partner_id>/stage
{ "stage_id": <consumer_won_stage_id> }
```
**Expected:** `400` or `422` — stage does not belong to this contact's pipeline

---

## 5. CONTACT INTERACTIONS

> Create a fresh consumer contact for this section so the timeline is clean.

```
POST /contacts
{
  "contact_type": "consumer",
  "name": "Test Interaction",
  "phone": "9000000001",
  "district": "Kozhikode",
  "address": "X"
}
```
**Save:** `cid = <id>`

### 5.1 Log an activity
```
POST /contacts/<cid>/interactions/log
{
  "activity_type": "call",
  "notes": "Called client. Interested in 5000L tank.",
  "outcome": "done"
}
```
**Expected:** `201`, `kind: "log"`, `entity_type: "contact"`

### 5.2 Missing notes → 422
```
POST /contacts/<cid>/interactions/log
{
  "activity_type": "call",
  "notes": ""
}
```
**Expected:** `422`

### 5.3 `other` activity type requires custom_label
```
POST /contacts/<cid>/interactions/log
{
  "activity_type": "other",
  "notes": "Some notes"
}
```
**Expected:** `422`

```
POST /contacts/<cid>/interactions/log
{
  "activity_type": "other",
  "custom_label": "Demo video sent",
  "notes": "Sent product demo video via WhatsApp"
}
```
**Expected:** `201`

### 5.4 Schedule a task (follow-up)
```
POST /contacts/<cid>/interactions/task
{
  "activity_type": "call",
  "title": "Follow-up call next week",
  "notes": "Client asked for a callback to confirm quote",
  "due_at": "2030-01-10T10:00:00Z",
  "assigned_to": <staff_employee_id>
}
```
**Expected:** `201`, `kind: "task"`, `status: null` or `"pending"`  
**Save:** `task_iid = <id>`

### 5.5 List interactions
```
GET /contacts/<cid>/interactions
```
**Expected:** list with both the log and the task

```
GET /contacts/<cid>/interactions?kind=log
```
**Expected:** only log entries

```
GET /contacts/<cid>/interactions?kind=task
```
**Expected:** only task entries

### 5.6 Complete the task
```
PATCH /contact-interactions/<task_iid>
{
  "status": "completed",
  "outcome": "done",
  "notes": "Spoke with client. Confirmed order."
}
```
**Expected:** `200`, `status: "completed"`

### 5.7 Reschedule a pending task
Create another task first:
```
POST /contacts/<cid>/interactions/task
{
  "activity_type": "visit",
  "title": "Site visit",
  "notes": "Visit the installation site",
  "due_at": "2030-02-01T09:00:00Z",
  "assigned_to": <staff_employee_id>
}
```
**Save:** `visit_iid = <id>`

```
PATCH /contacts/<cid>/interactions/<visit_iid>/reschedule
{
  "due_at": "2030-02-15T09:00:00Z",
  "notes": "Client asked to push visit by two weeks"
}
```
**Expected:** `200`, `due_at` updated

### 5.8 Reassign a task (head only)

As **staff**: 
```
PATCH /contacts/<cid>/interactions/<visit_iid>/reassign
{ "assigned_to": <other_employee_id> }
```
**Expected:** `403 Forbidden`

As **admin/manager**:
```
PATCH /contacts/<cid>/interactions/<visit_iid>/reassign
{ "assigned_to": <other_employee_id> }
```
**Expected:** `200`, `assigned_to` updated

### 5.9 Delete an interaction (head only)

As **staff**:
```
DELETE /contacts/<cid>/interactions/<visit_iid>
```
**Expected:** `403`

As **admin/manager**:
```
DELETE /contacts/<cid>/interactions/<visit_iid>
```
**Expected:** `204 No Content`

---

## 6. TIMELINE

```
GET /contacts/<cid>/timeline
```
**Expected:**
- `200`, array of entries, newest first
- After the stage move from §4: an entry with `action: "stage_changed"`
- After the log from §5.1: an entry for the activity
- After completing the task from §5.6: a completion entry

**Check order:** `created_at` of entry[0] ≥ `created_at` of entry[1]

---

## 7. RBAC — Staff visibility rules

Log in as a **staff** employee (sales_executive).

### 7.1 Staff sees contacts assigned to them
```
GET /contacts/<contact_assigned_to_this_staff>
```
**Expected:** `200`

### 7.2 Staff cannot see contacts assigned to someone else
```
GET /contacts/<contact_assigned_to_other_staff>
```
**Expected:** `403` or `404`

### 7.3 Staff sees contacts they created
Even if `assigned_to` is someone else, the creator can still read:
```
GET /contacts/<contact_created_by_this_staff>
```
**Expected:** `200`

### 7.4 Staff can see contact with an open task assigned to them
1. Admin creates contact and assigns to other staff
2. Admin schedules a task on that contact, `assigned_to: <this staff>`
3. Staff can now `GET /contacts/<that_contact_id>` → **Expected:** `200`

### 7.5 Staff cannot log on a contact they don't own
```
POST /contacts/<unrelated_contact_id>/interactions/log
{ "activity_type": "call", "notes": "Sneaky log" }
```
**Expected:** `403`

---

## 8. Pipeline Board (Web)

Open `http://localhost:3000/dashboard/pipeline` while logged in.

| # | Action | Expected |
|---|--------|----------|
| 8.1 | Page loads | Consumer tab active, columns from seeded stages |
| 8.2 | Click **Partner** tab | Columns refresh for partner pipeline |
| 8.3 | Click **Dealer** tab | Columns refresh for dealer pipeline |
| 8.4 | Contact with stage set (§4) | Appears in correct column |
| 8.5 | Contact with no stage | Appears in "No Stage" column (leftmost) |
| 8.6 | Sidebar link | Pipeline link highlighted, correct icon |
| 8.7 | Sidebar collapsed | Pipeline icon-only link still visible |

---

## Checklist summary

- [ ] Unauthenticated → 401
- [ ] Consumer / Partner / Dealer contact creation
- [ ] Duplicate phone → 409
- [ ] Get / update / soft-delete
- [ ] Pipeline stages seeded (10 / 9 / 6)
- [ ] Set stage, verify via GET
- [ ] Terminal stage without loss_reason → 422
- [ ] Wrong-type stage → 400/422
- [ ] Log activity
- [ ] Empty notes → 422
- [ ] Schedule task
- [ ] Complete task
- [ ] Reschedule task
- [ ] Reassign (head only)
- [ ] Delete interaction (head only)
- [ ] Timeline newest-first, stage_changed entry present
- [ ] Staff RBAC — assigned visible, unrelated blocked
- [ ] Staff gains access via open task
- [ ] Pipeline board tabs switch correctly
