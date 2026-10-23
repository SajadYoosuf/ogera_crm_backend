# Sprint 2 — Contact Creation Auto Follow-up (48h Rule)

## Why
When a sales employee creates a new contact, the clock on the first follow-up starts immediately.
Without an automatic reminder, leads go cold because the employee forgets to schedule the first call.
Admin/manager contacts need a different path: they don't carry leads themselves, so a prompt lets them
choose who to assign the first follow-up to.

## What changes

### Backend (`routes/contacts.py`)
- After a contact is inserted and `db.flush()` runs (so `contact.id` exists), check `current_user.role`.
- If the creator is NOT in `NON_ASSIGNABLE_ROLES` (i.e. they execute work):
  auto-create a `FollowupSchedule` row: `assigned_to = current_user.id`, `due_at = now + 48h`,
  `channel = 'phone_call'`, `priority = 'high'`, `title = 'First Contact'`, `status = 'todo'`,
  `created_by = current_user.id`.
- If the creator IS in `NON_ASSIGNABLE_ROLES` (admin/manager): no auto-schedule. The response
  includes a new flag `auto_followup_needed: true` so the web can show a prompt.
- Config constant `AUTO_FOLLOWUP_HOURS` added to `config.py` (default 48).

### Backend (`schemas/contact.py`)
- `ContactResponse` gains `auto_followup_needed: bool = False`.

### Web (`src/features/contacts/`)
- After a successful `POST /contacts`, if `auto_followup_needed == true` in the response,
  show a modal: "Schedule First Follow-up?" with fields: Assigned To (non-monitoring employees),
  Due Date (pre-filled +48h), Due Time (optional), Channel (default phone_call).
  "Skip" closes it without scheduling. "Schedule" calls `POST /contacts/{id}/followups`.
- This modal is a new component: `AutoFollowupPromptModal.tsx`.

## Out of scope
- Mobile: auto follow-up for staff already happens on the backend silently. No mobile UI change needed.
- No change to the 48h duration for existing manually scheduled follow-ups.

## Verification
- Create a contact as a sales_executive → `followup_schedule` has one row for that contact with `due_at ≈ now+48h`.
- Create a contact as admin/manager → no `followup_schedule` row; response has `auto_followup_needed: true`.
- Web: create as admin/manager → prompt modal appears; "Skip" closes it; "Schedule" creates follow-up.
- Web: create as sales_executive (not applicable — web is admin/manager only per rule 11, so the auto-schedule backend path handles all mobile staff).
- Tests: `test_contacts_module.py` — two new cases.
