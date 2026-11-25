# 2026-11-25 — WhatsApp connection settings (backend)

Stores each tenant's WhatsApp Business API credentials so template management and sending can use
the tenant's own Meta account. This is step 1 of the WhatsApp template work: the web panel, the
webhook change and the template routes come next.

## What was added
- `scripts/whatsapp_config.sql` — **run by hand** (not yet applied anywhere):
  - `whatsapp_config` in each tenant schema, one row per tenant (unique index on `(TRUE)`).
  - `public.whatsapp_tenant_map` (`waba_id` → `schema_name`) so an inbound webhook, which has no login,
    can find the tenant.
- `models/whatsapp_config.py` — `WhatsAppConfig` (tenant) and `WhatsAppTenantMap` (public).
- `services/crypto.py` — AES-256-GCM for the access token and app secret. Needs
  `WHATSAPP_ENCRYPTION_KEY` (32 random bytes, base64). **Back it up**: if lost, every tenant re-enters its token.
- `services/whatsapp_config_service.py` — save/read/test/subscribe/rotate/delete, and
  `get_credentials(db)` for server-side Meta calls (decrypted; never return it from a route).
- `routes/whatsapp_config.py` — `GET/PUT/DELETE /whatsapp/config`, `POST /whatsapp/config/test`,
  `/subscribe`, `/rotate-verify-token`. Uses the existing `integrations` permission (r to view, u to change).
- `tests/test_whatsapp_config.py` — 18 tests; Meta is faked.

## Behaviour to know
- Secrets are never returned. The response has `has_access_token`, `has_app_secret` and a masked
  `access_token_hint` (`EAAG…x7Qz`). A blank secret on update keeps the saved one.
- Changing the phone number, WABA id or token resets `status` to `disconnected`; "Test connection" sets
  `connected` or `error` and stores the display number, verified name and quality rating.
- A WABA id or phone number already mapped to another tenant gives 409.
- The map row (public) and the config row (tenant) are written in two separate transactions.
- The webhook URL shown to the user is `{PUBLIC_API_URL or request base}/webhooks/whatsapp`.

## Not done yet
- `routes/webhooks.py`, `services/whatsapp_service.py` and `routes/lead_whatsapp.py` still read the
  `WHATSAPP_*` environment variables. They need to switch to `get_credentials()` and, for webhooks,
  to resolve the tenant from `whatsapp_tenant_map` and check the signature with that tenant's app secret.
- Web panel (Settings → WhatsApp Connection).
- Add `PUBLIC_API_URL` and `WHATSAPP_ENCRYPTION_KEY` to the deployed environment.
