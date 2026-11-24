"""
Lead WhatsApp inbox router.

ENDPOINTS:
    GET  /leads/{lead_id}/whatsapp-messages          — list messages (newest first)
    POST /leads/{lead_id}/whatsapp-reply             — send a free-text or template reply

The whatsapp_messages table must exist (see models/whatsapp_message.py for the
CREATE TABLE statement).

Authentication: same JWT as all other lead routes. Staff can only access leads
assigned to them.
"""
import logging
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from auth import get_current_user
from database import get_session_for_schema
from models.employee import Employee
from models.whatsapp_message import WhatsAppMessage
import services.lead_service as lead_svc
from services.lead_access import ensure_can_work_lead

logger = logging.getLogger(__name__)

router = APIRouter(tags=["lead-whatsapp"])

WHATSAPP_ACCESS_TOKEN    = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_API_VERSION     = "v18.0"
WHATSAPP_API_URL         = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}"
WHATSAPP_TEMPLATE_LANGUAGE = os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "en_US")


class ReplyBody(BaseModel):
    message: str | None = None          # free-text (24-hour window only)
    template_name: str | None = None    # template name if sending outside 24h window
    template_params: list[str] = []     # positional params for template body variables


@router.get("/leads/{lead_id}/whatsapp-messages", summary="List WhatsApp messages for a lead")
def list_messages(
    lead_id: int,
    current_user: Employee = Depends(get_current_user),
):
    schema = current_user._tenant_schema
    try:
        with get_session_for_schema(schema) as db:
            lead = lead_svc.get_lead(db, lead_id)
            ensure_can_work_lead(current_user, lead)

            rows = db.scalars(
                select(WhatsAppMessage)
                .where(WhatsAppMessage.lead_id == lead_id)
                .order_by(WhatsAppMessage.created_at.asc())
            ).all()
            return [_to_dict(r) for r in rows]
    except OperationalError as exc:
        logger.error("list_messages — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")


@router.post("/leads/{lead_id}/whatsapp-reply", status_code=201, summary="Send a WhatsApp reply")
def send_reply(
    lead_id: int,
    body: ReplyBody,
    current_user: Employee = Depends(get_current_user),
):
    if not body.message and not body.template_name:
        raise HTTPException(400, detail="Provide either message or template_name")
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        raise HTTPException(503, detail="WhatsApp is not configured on this server")

    schema = current_user._tenant_schema
    try:
        with get_session_for_schema(schema) as db:
            lead = lead_svc.get_lead(db, lead_id)
            ensure_can_work_lead(current_user, lead)

            if not lead.phone:
                raise HTTPException(400, detail="This lead has no phone number")

            phone = lead.phone.lstrip("+")
            wamid = None

            if body.template_name:
                payload = _build_template_payload(phone, body.template_name, body.template_params)
                content_type = "template"
                content_body = None
            else:
                payload = {
                    "messaging_product": "whatsapp",
                    "to": phone,
                    "type": "text",
                    "text": {"body": body.message},
                }
                content_type = "text"
                content_body = body.message

            # Send to Meta
            try:
                resp = httpx.post(
                    f"{WHATSAPP_API_URL}/{WHATSAPP_PHONE_NUMBER_ID}/messages",
                    headers={"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"},
                    json=payload,
                    timeout=10,
                )
                resp.raise_for_status()
                wamid = resp.json().get("messages", [{}])[0].get("id")
            except Exception as exc:
                logger.error("WhatsApp send failed: %s", exc)
                raise HTTPException(502, detail=f"WhatsApp API error: {exc}")

            msg = WhatsAppMessage(
                lead_id=lead_id,
                direction="out",
                wamid=wamid,
                content_type=content_type,
                body=content_body,
                template_name=body.template_name,
                sent_by=current_user.id,
                status="sent",
            )
            db.add(msg)
            db.commit()
            db.refresh(msg)
            return _to_dict(msg)
    except OperationalError as exc:
        logger.error("send_reply — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")


def _build_template_payload(to: str, template_name: str, params: list[str]) -> dict:
    components = []
    if params:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": p} for p in params],
        })
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": WHATSAPP_TEMPLATE_LANGUAGE},
            "components": components,
        },
    }


def _to_dict(m: WhatsAppMessage) -> dict:
    return {
        "id": m.id,
        "lead_id": m.lead_id,
        "direction": m.direction,
        "wamid": m.wamid,
        "content_type": m.content_type,
        "body": m.body,
        "template_name": m.template_name,
        "sent_by": m.sent_by,
        "status": m.status,
        "created_at": m.created_at,
    }
