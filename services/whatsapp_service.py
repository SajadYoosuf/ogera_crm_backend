"""
WhatsApp service — sends template messages via the WhatsApp Business API.

WHAT IS A TEMPLATE MESSAGE?
----------------------------
WhatsApp only allows businesses to send the first message to a customer using
a pre-approved "template". Templates are submitted to Meta for review and must
contain no promotional or spammy content.

The template used here is called `welcome_lead` and it looks like:
  "Hello {first_name}, thanks for reaching out! Our team will contact you shortly."

The {first_name} is a template variable — we fill it in at send time.

HOW IT WORKS:
  1. A lead is created (by any source).
  2. `create_lead()` in lead_service.py queues `send_lead_welcome` as a background task.
  3. This function runs after the HTTP response is returned to the caller.
  4. It calls the WhatsApp Business API (`graph.facebook.com/v18.0/{phone_id}/messages`).
  5. On success: marks lead.whatsapp_sent=True.
  6. On failure: logs the error and continues — NEVER fails the lead creation.

WHY BEST-EFFORT?
  The WhatsApp send is a secondary side effect. If the API is temporarily down,
  or the number is not on WhatsApp, we still want the lead to be created.
  Staff can manually retry via POST /leads/{id}/send-whatsapp.

ENVIRONMENT VARIABLES REQUIRED:
  WHATSAPP_ACCESS_TOKEN      — Meta system user or user access token
  WHATSAPP_PHONE_NUMBER_ID   — The WhatsApp phone number ID from your WABA
  WHATSAPP_WELCOME_TEMPLATE  — Template name (default: welcome_lead)
"""

import logging
import os

import httpx

from database import get_session_for_schema

logger = logging.getLogger(__name__)

WHATSAPP_TEMPLATE_SENDING_ENABLED = os.getenv("WHATSAPP_TEMPLATE_SENDING_ENABLED", "0") == "1"

WHATSAPP_API_VERSION = "v18.0"
WHATSAPP_API_URL = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}"
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_WELCOME_TEMPLATE = os.getenv("WHATSAPP_WELCOME_TEMPLATE", "welcome")
WHATSAPP_TEMPLATE_LANGUAGE = os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "en_US")
# Image URL for the header component of the welcome template.
# The "welcome" template has an image header — this URL is passed to WhatsApp
# at send time so Meta fetches and displays the image in the message.
WHATSAPP_WELCOME_HEADER_IMAGE = os.getenv("WHATSAPP_WELCOME_HEADER_IMAGE", "")


def _to_wa_number(phone: str) -> str:
    """
    Convert a phone number to the format WhatsApp expects: digits only, no +.

    Examples:
        "9000000003"      → "919000000003"  (adds India country code)
        "+91 9000000003"  → "919000000003"  (strips + and spaces)
        "919000000003"    → "919000000003"  (already correct)
    """
    digits = "".join(ch for ch in phone if ch.isdigit())
    # If the number is 10 digits, assume India (+91)
    if len(digits) == 10:
        digits = "91" + digits
    return digits


def send_lead_welcome(lead_id: int, schema: str) -> None:
    """
    Background task — sends a WhatsApp welcome template to a newly created lead.

    This runs AFTER the HTTP response is sent back to the caller (FastAPI BackgroundTasks).
    It opens its own DB session because the request session has already closed.

    Parameters:
        lead_id — the ID of the Lead row (in the tenant's schema)
        schema  — the tenant's PostgreSQL schema name
    """
    if not WHATSAPP_TEMPLATE_SENDING_ENABLED:
        return

    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        logger.warning(
            "WhatsApp not configured (WHATSAPP_ACCESS_TOKEN or WHATSAPP_PHONE_NUMBER_ID missing). "
            "Skipping welcome message for lead %s.", lead_id
        )
        return

    try:
        with get_session_for_schema(schema) as db:
            # Import here to avoid circular import at module level
            from models.lead import Lead
            import datetime

            lead = db.get(Lead, lead_id)
            if not lead:
                logger.warning("send_lead_welcome: lead %s not found in schema %s", lead_id, schema)
                return
            if not lead.phone:
                return  # No phone — nothing to send to
            if lead.whatsapp_sent:
                return  # Already sent — don't send twice

            wa_number = _to_wa_number(lead.phone)

            # Build the WhatsApp API request payload.
            # The "welcome" template has: image header (no variables), static body, URL button.
            # Components list only needs entries for slots that have dynamic values.
            # The body is fully static so no body component is needed.
            components = []
            if WHATSAPP_WELCOME_HEADER_IMAGE:
                # Image header — pass the CDN URL so Meta fetches and displays it
                components.append({
                    "type": "header",
                    "parameters": [
                        {"type": "image", "image": {"link": WHATSAPP_WELCOME_HEADER_IMAGE}}
                    ],
                })

            payload = {
                "messaging_product": "whatsapp",
                "to": wa_number,
                "type": "template",
                "template": {
                    "name": WHATSAPP_WELCOME_TEMPLATE,
                    "language": {"code": WHATSAPP_TEMPLATE_LANGUAGE},
                    "components": components,
                },
            }

            resp = httpx.post(
                f"{WHATSAPP_API_URL}/{WHATSAPP_PHONE_NUMBER_ID}/messages",
                json=payload,
                headers={
                    "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )

            if resp.status_code == 200:
                lead.whatsapp_sent = True
                lead.whatsapp_sent_at = datetime.datetime.now(datetime.timezone.utc)
                db.commit()
                logger.info("WhatsApp welcome sent to lead %s (%s)", lead_id, wa_number)
            else:
                # Log but don't raise — lead creation must not be rolled back
                logger.warning(
                    "WhatsApp send failed for lead %s: HTTP %s — %s",
                    lead_id, resp.status_code, resp.text[:200],
                )

    except Exception:
        # Catch-all: a background task failure must never crash the worker
        logger.exception("Unexpected error in send_lead_welcome for lead %s", lead_id)


def send_whatsapp_template(phone: str, template_name: str, params: list[str]) -> bool:
    """
    Generic helper — send any approved template to a phone number.

    Returns True on success, False on failure.
    Used by the manual "Send WhatsApp" button in the lead detail view.
    """
    if not WHATSAPP_TEMPLATE_SENDING_ENABLED:
        logger.info("WhatsApp template sending is disabled; not sending %s", template_name)
        return False

    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        logger.warning("WhatsApp not configured — cannot send template %s", template_name)
        return False

    wa_number = _to_wa_number(phone)
    payload = {
        "messaging_product": "whatsapp",
        "to": wa_number,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": WHATSAPP_TEMPLATE_LANGUAGE},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": p} for p in params],
                }
            ],
        },
    }

    try:
        resp = httpx.post(
            f"{WHATSAPP_API_URL}/{WHATSAPP_PHONE_NUMBER_ID}/messages",
            json=payload,
            headers={"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"},
            timeout=10.0,
        )
        if resp.status_code == 200:
            return True
        logger.warning("WhatsApp template %s failed: %s", template_name, resp.text[:200])
        return False
    except Exception:
        logger.exception("Error sending WhatsApp template %s", template_name)
        return False
