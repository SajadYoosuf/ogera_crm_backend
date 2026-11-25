# WhatsApp inbox (backend)

Cross-lead WhatsApp inbox for the sidebar page, plus the missing piece underneath it: inbound
customer messages were never saved.

## What changed
- `scripts/migrations/004_whatsapp_inbox.sql` — creates `whatsapp_messages` if missing, adds `is_read`
  (default TRUE; existing inbound rows become unread) and the inbox indexes. **Run once per tenant schema.**
- `models/whatsapp_message.py` — `is_read` column. `routes/lead_whatsapp.py` returns it.
- `routes/webhooks.py` — `_process_whatsapp_lead` now stores each inbound message (`direction='in'`,
  `is_read=False`, deduped by `wamid` because Meta retries) and creates a `whatsapp_inbound` notification
  for every head and the lead's assignee.
- `services/lead_access.py` — `head_employee_ids(db)`: active employees whose role has `see_all_records`.
- `routes/whatsapp_inbox.py` (new), prefix `/whatsapp-inbox`:
  - `GET /conversations` (page, page_size, search) — one row per lead with messages, newest first.
  - `GET /unread-count` — conversations with unread inbound messages (sidebar badge).
  - `POST /conversations/{lead_id}/mark-read`
  - `POST /conversations/{lead_id}/assign` — head only; reuses `lead_svc.update_lead` + `notify_lead_assigned`.

## Access
Same as leads: `roles.see_all_records` (admin, manager) sees all; everyone else only leads assigned to them.
Reading needs `leads:r`. Messages of one conversation still use `GET/POST /leads/{id}/whatsapp-*`.

## Known issues found, not fixed
- `notification_service.notify_roles` filters on `Employee.role`, which no longer exists (employees have
  `role_id`), so it logs an error and sends nothing. The inbox does not use it.
- `Employee.fcm_token` is read by `dispatch_notification_push` but is not a model field.
- `tests/seed_data.py` does not set `see_all_records` for admin/manager, so many older tests fail.
  `tests/test_whatsapp_inbox.py` sets it in its own fixture.

## Tests
`tests/test_whatsapp_inbox.py` (15): role scope, ordering, unread counts, mark-read, assignment, auth,
and the webhook (new lead, redelivery, existing lead + assignee notification).
