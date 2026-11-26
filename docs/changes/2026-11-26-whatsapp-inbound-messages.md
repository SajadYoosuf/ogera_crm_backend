# WhatsApp: save incoming messages and fix the inbox list (backend)

[2026-11-25-whatsapp-inbox.md](2026-11-25-whatsapp-inbox.md) describes storing incoming messages, but that code
was not in the branch: nothing saved them, and `GET /whatsapp-inbox/conversations` returned 500
(`WhatsAppMessage` has no attribute `is_read`).

## What changed
- `routes/whatsapp_inbox.py` — the conversation list counts unread by `status != "read"`, the same rule
  `unread-count` and `mark-read` already use.
- `routes/webhooks.py`
  - Each incoming message of a new or existing lead is saved (`direction="in"`, `status="received"`).
  - A message id that is already saved is ignored, so a Meta re-delivery changes nothing.
  - Admins, managers and the lead's assignee get a `whatsapp_inbound` notification.
  - The automatic move from `new` to `contacted` is now written to the lead timeline (no actor).
  - The WABA id is looked up in `public.whatsapp_tenant_map` (saved from Settings) before `public.tenants`.
- `routes/lead_whatsapp.py` — messages return `is_read`, derived from `status`. The web thread needs it.
- `tests/test_whatsapp_inbox.py` — moved from `is_read` to `status`, repaired stale setup, added 4 tests.

This replaces the `is_read` column design in the 2026-11-25 note. The model has no such column.

## Access / data impact
- No schema change and no migration. `whatsapp_messages` must already exist
  (`scripts/migrations/004_whatsapp_inbox.sql` creates it). If its `is_read` column was added, it is not used.
- Any older incoming row whose `status` is not `read` counts as unread.
- Every incoming message now creates a notification (and a push, where set up) for each admin, each manager
  and the assignee.
- Conversation visibility is unchanged.

## Verification
- On in-memory SQLite, each file run alone: `test_whatsapp_inbox.py` 19 passed, `test_whatsapp_config.py`
  18 passed, `test_whatsapp_templates.py` 43 passed.
- All test files, run one at a time with and without this change: 176 failures without, 162 with. Four tests
  failed only in the "with" run. None of them uses the changed routes; repeated 8 times each way, one failed
  intermittently both ways, one failed once with the change only, and two did not fail again. The likely cause
  (not confirmed for each) is the push thread sharing the single SQLite connection.
- A single `pytest tests` run fails far more tests, including the WhatsApp files, with and without this change.
- Not run against PostgreSQL, a live database or Meta. Not checked in the web app.

## Known issues or not done yet
- A message from a number that is only a contact (no lead) is still dropped: a message row needs a lead.
- No unique index on `wamid`, so two re-deliveries handled at the same moment could both be saved.
- Template approval webhooks are not applied: the webhook route never calls
  `whatsapp_template_service.apply_webhook_change` (the tests cover the service only).
- Delivery and read receipts for outgoing messages are not processed.
- The webhook verify token and signature secret, and the send credentials, come from environment variables,
  not from the connection saved in Settings. With no `WHATSAPP_APP_SECRET`, signatures are not checked.
