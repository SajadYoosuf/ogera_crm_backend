# 2026-11-25 — WhatsApp template management (backend)

Create, edit, send to Meta, sync and delete WhatsApp message templates per tenant. Ported from the
WhatsApp Business app (`ogera-crm-whatsapp-business`: `template-validators`, `template-components`,
`template-status-normalize`, `template-webhook`, `meta-api`). Needs the connection settings from
`2026-11-25-whatsapp-connection-settings.md`.

## Added
- `scripts/whatsapp_templates.sql` — **run by hand, after `whatsapp_config.sql`**: `whatsapp_templates` per tenant schema
  (unique on name + language, status and category checks) and optional `template_id` / `template_params` on `whatsapp_messages`.
- `models/whatsapp_template.py`, `schemas/whatsapp_template.py`.
- `services/whatsapp_template_rules.py` — pure rules and payload building: name, body (1024, no gaps in `{{n}}`), footer (60),
  header, buttons, sample values; Meta `components` builder and parser; status words.
- `services/whatsapp_meta_templates.py` — Graph calls: submit, edit, delete, list (paged).
- `services/whatsapp_template_service.py` — create draft, edit, submit, delete, sync, webhook updates.
- `routes/whatsapp_templates.py` — `GET/POST /whatsapp/templates`, `GET/PUT/DELETE /whatsapp/templates/{id}`,
  `POST /{id}/submit`, `POST /sync`. Permission: `integrations` (r to view, u to change).
- `routes/webhooks.py` — `message_template_*` events now update templates; the WABA → tenant lookup checks
  `whatsapp_tenant_map` first (the old `Tenant.whatsapp_account_id` column does not exist).
- `tests/test_whatsapp_templates.py` — 46 tests, Meta faked.

## Behaviour to know
- A template is a DRAFT (local) until submitted. Meta then owns the status; it follows through the webhook or Sync.
- Submitted templates keep their name and language. Editing one sends it back to Meta and sets it to PENDING.
  No edits while PENDING, IN_APPEAL, DISABLED or PENDING_DELETION.
- Authentication templates are refused for now (as in the source app).
- 100 submits per hour is enforced locally before Meta is called.
- Deleting sends the template id to Meta (`hsm_id`) so other languages with the same name stay.
- `WHATSAPP_TEMPLATES_DRY_RUN=1` skips every Meta call, for trying the screens without a Meta app.
- Unknown template in a webhook (made in Business Manager) creates a stub with an empty body; Sync fills it in.

## Not done yet
- Image / video / document header upload (Meta resumable upload). Headers take a sample link or an existing handle.
- Sending an approved template from the lead inbox (picker + variable inputs), and moving `welcome_lead`
  / `lead_whatsapp.py` / the webhook's signature check off the `WHATSAPP_*` environment variables.
- The Meta app must subscribe to `message_template_status_update` and `message_template_quality_update`
  (WhatsApp → Configuration → Webhook fields); until then use Sync.
