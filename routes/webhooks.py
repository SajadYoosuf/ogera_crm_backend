"""
Webhooks router — receives inbound events from external services.

CURRENTLY HANDLES:
  WhatsApp Business API webhook — inbound messages from customers on WhatsApp.

HOW WHATSAPP WEBHOOKS WORK:
  1. Meta calls GET /webhooks/whatsapp with hub.mode, hub.verify_token, hub.challenge.
     We echo back hub.challenge to prove we control the endpoint.
  2. After verification, Meta sends POST /webhooks/whatsapp for every message event.
     Each POST contains a JSON payload describing the inbound message.
  3. We verify the HMAC-SHA256 signature (X-Hub-Signature-256 header) to confirm
     the request genuinely came from Meta (not a forged request).
  4. We extract the sender's phone number and check if they already exist as a lead
     or contact in the tenant's schema.
  5. If new: create a lead (source="whatsapp") and trigger a WhatsApp auto-reply.
  6. If existing lead: update status to "contacted".
  7. We always return HTTP 200 immediately — if we return anything else, Meta
     retries endlessly.

MULTI-TENANT WHATSAPP:
  Each tenant has their own WhatsApp Business Account (WABA). To route an inbound
  message to the correct tenant schema, we use the WhatsApp Account ID (`wa_id`)
  in the webhook payload. The public.tenants table must have a `whatsapp_account_id`
  column that maps WABA IDs to schemas.

  Alternatively (simpler for single-tenant): use a single WABA and configure the
  WABA ID via environment variable WHATSAPP_DEFAULT_SCHEMA.

ENVIRONMENT VARIABLES:
  WHATSAPP_VERIFY_TOKEN   — the token you set in the Meta Developer Console webhook config
  WHATSAPP_APP_SECRET     — the App Secret from your Meta App (used for HMAC verification)
  WHATSAPP_DEFAULT_SCHEMA — fallback tenant schema if WABA ID is not in the tenants table
"""

import hashlib
import hmac
import json
import logging
import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from database import get_public_db_ctx, get_session_for_schema
from models.tenant import Tenant
import services.lead_service as lead_svc

logger = logging.getLogger(__name__)

WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
WHATSAPP_DEFAULT_SCHEMA = os.getenv("WHATSAPP_DEFAULT_SCHEMA", "")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ── HMAC verification helper ──────────────────────────────────────────────────

def _verify_whatsapp_signature(body: bytes, signature_header: str) -> bool:
    """
    Verify that the POST body was sent by Meta (not forged).

    HOW HMAC WORKS:
      Meta computes: HMAC-SHA256(app_secret, request_body)
      They send it as: X-Hub-Signature-256: sha256=<hex_digest>
      We compute the same HMAC and compare. If they match, the request is genuine.

    WHY NOT JUST TRUST THE REQUEST?
      Without this check, anyone who discovers our webhook URL can send fake
      lead data or spam our system. The HMAC ties each request to a secret
      that only Meta and we know.
    """
    if not WHATSAPP_APP_SECRET:
        # If the secret is not configured, skip verification (dev mode only).
        logger.warning("WHATSAPP_APP_SECRET not set — skipping signature verification")
        return True

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = "sha256=" + hmac.new(
        WHATSAPP_APP_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    # compare_digest prevents timing attacks (comparing character by character
    # could reveal how many characters matched if we used ==)
    return hmac.compare_digest(signature_header, expected)


def _get_schema_for_waba(wa_id: str) -> str | None:
    """
    Look up which tenant schema corresponds to a WhatsApp Business Account ID.

    Returns the schema_name, or falls back to WHATSAPP_DEFAULT_SCHEMA if configured.
    Returns None if no match and no default is configured.
    """
    if not wa_id:
        return WHATSAPP_DEFAULT_SCHEMA or None

    try:
        with get_public_db_ctx() as db:
            tenant = db.scalar(
                select(Tenant).where(Tenant.whatsapp_account_id == wa_id)
            )
            if tenant:
                return tenant.schema_name
    except Exception:
        logger.exception("Error looking up WABA %s in tenants table", wa_id)

    return WHATSAPP_DEFAULT_SCHEMA or None


# ── Webhook verification (GET) ────────────────────────────────────────────────

@router.get(
    "/whatsapp",
    response_class=PlainTextResponse,
    summary="WhatsApp webhook verification (Meta calls this once during setup)",
)
def verify_whatsapp_webhook(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
):
    """
    Meta calls this endpoint once to verify our webhook URL is real.

    When you configure a webhook in the Meta Developer Console, Meta sends this
    GET request with hub.mode="subscribe" and your verify_token.
    We must echo back hub.challenge to confirm ownership.
    """
    if hub_mode == "subscribe" and hub_verify_token == WHATSAPP_VERIFY_TOKEN:
        logger.info("WhatsApp webhook verified")
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


# ── Inbound message handler (POST) ────────────────────────────────────────────

@router.post(
    "/whatsapp",
    summary="Receive inbound WhatsApp messages",
)
async def receive_whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receive and process inbound WhatsApp messages.

    Meta sends a POST here for every message received by the WhatsApp Business Account.
    We always return HTTP 200 — if we return any other status, Meta will retry the
    same event up to 20 times over 24 hours, filling our DB with duplicates.

    WHAT WE DO WITH EACH MESSAGE:
      1. Verify the HMAC signature.
      2. Parse the sender's phone number from the payload.
      3. Find the tenant schema via WABA ID.
      4. Check if phone is already a lead or contact.
      5. If new: create a lead + send welcome message.
      6. If existing lead: update status to "contacted" (they reached out again).
    """
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not _verify_whatsapp_signature(body, signature):
        logger.warning("WhatsApp webhook: invalid HMAC signature — ignoring")
        # Return 200 anyway to prevent Meta from retrying
        return {"status": "ignored"}

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("WhatsApp webhook: could not parse JSON body")
        return {"status": "ignored"}

    # Parse Meta's nested payload structure:
    # entry[] → changes[] → value → messages[]
    for entry in data.get("entry", []):
        waba_id = entry.get("id", "")  # WhatsApp Business Account ID
        schema = _get_schema_for_waba(waba_id)
        if not schema:
            logger.warning("WhatsApp webhook: no schema found for WABA %s — skipping", waba_id)
            continue

        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])

            for msg in messages:
                sender_phone = msg.get("from", "")
                msg_type = msg.get("type", "")
                msg_text = ""
                if msg_type == "text":
                    msg_text = msg.get("text", {}).get("body", "")

                if not sender_phone:
                    continue

                # Build raw_payload to preserve the original WhatsApp message
                raw_payload = {
                    "source": "whatsapp_inbound",
                    "waba_id": waba_id,
                    "message_id": msg.get("id"),
                    "message_type": msg_type,
                    "message_text": msg_text,
                    "timestamp": msg.get("timestamp"),
                }

                background_tasks.add_task(
                    _process_whatsapp_lead,
                    sender_phone,
                    raw_payload,
                    schema,
                )

    return {"status": "ok"}


def _process_whatsapp_lead(phone: str, raw_payload: dict, schema: str) -> None:
    """
    Background task — decides whether to create a new lead from a WhatsApp message.

    WHY A BACKGROUND TASK?
      The webhook handler must return quickly (Meta times out after a few seconds).
      DB queries and WhatsApp API calls can take longer, so we defer them.
    """
    from services.whatsapp_service import send_lead_welcome

    try:
        from phone_utils import phone_key as compute_phone_key
        from sqlalchemy import or_
        from sqlalchemy import select

        pk = compute_phone_key(phone) if len(phone) >= 7 else None
        if not pk:
            return

        with get_session_for_schema(schema) as db:
            from models.lead import Lead
            from models.contact import Contact

            # Check for an existing lead with this phone
            existing_lead = db.scalar(
                select(Lead)
                .where(Lead.phone_key == pk)
                .where(Lead.deleted_at.is_(None))
                .limit(1)
            )

            if existing_lead:
                # They messaged again — update status to "contacted" if still "new"
                if existing_lead.status == "new":
                    existing_lead.status = "contacted"
                    db.commit()
                return  # Don't create a duplicate lead

            # Check for existing contact (they're already in the CRM)
            existing_contact = db.scalar(
                select(Contact)
                .where(Contact.phone_key == pk)
                .where(Contact.deleted_at.is_(None))
                .limit(1)
            )
            if existing_contact:
                return  # Don't create a lead for someone already in contacts

            # New person — create a lead
            lead = lead_svc.create_lead(
                db=db,
                schema=schema,
                first_name="WhatsApp",  # We don't have a name yet
                last_name="Enquiry",
                source_id=5,  # WhatsApp
                phone=phone,
                raw_payload=raw_payload,
                notes=raw_payload.get("message_text") or None,
            )
            # Send welcome reply (don't use BackgroundTasks here — we're already in a background task)
            send_lead_welcome(lead.id, schema)

    except Exception:
        logger.exception("Error processing WhatsApp lead for phone %s in schema %s", phone, schema)
