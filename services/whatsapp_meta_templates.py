"""
Meta Graph API calls for message templates. Ported from meta-api.ts in the WhatsApp Business app.

Every function takes the tenant's decrypted WhatsAppCredentials and raises MetaApiError with a
sentence that is safe to show to the user. The access token is never put in an error or a log.
"""
import logging

import httpx

from services.whatsapp_config_service import WhatsAppCredentials

logger = logging.getLogger(__name__)

TIMEOUT = 30.0
PAGE_LIMIT = 100
TEMPLATE_FIELDS = "id,name,language,status,category,components,quality_score"


class MetaApiError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _error_text(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", {})
    except ValueError:
        err = {}
    # error_user_msg is Meta's plain-language reason; message is the technical one
    return err.get("error_user_msg") or err.get("message") or f"Meta returned HTTP {resp.status_code}"


def _call(method: str, url: str, creds: WhatsAppCredentials, **kwargs) -> httpx.Response:
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            return client.request(method, url, headers={"Authorization": f"Bearer {creds.access_token}"}, **kwargs)
    except httpx.HTTPError as exc:
        logger.warning("Meta template call could not reach Meta: %s", type(exc).__name__)
        raise MetaApiError("Could not reach Meta. Try again in a moment.")


def submit_template(creds: WhatsAppCredentials, payload: dict) -> dict:
    """POST /{waba}/message_templates. Returns {"id", "status", "category"}."""
    resp = _call("POST", f"{creds.base_url}/{creds.waba_id}/message_templates", creds, json=payload)
    if resp.status_code == 429:
        raise MetaApiError("Meta allows 100 new templates per hour. Wait a while and try again.", 429)
    if resp.status_code >= 400:
        raise MetaApiError(_error_text(resp), 400 if resp.status_code < 500 else 502)
    data = resp.json()
    if not data.get("id"):
        raise MetaApiError("Meta accepted the template but did not return an id.")
    return {"id": str(data["id"]), "status": data.get("status") or "PENDING", "category": data.get("category")}


def edit_template(creds: WhatsAppCredentials, meta_template_id: str, components: list[dict], category: str | None = None) -> None:
    """POST /{template_id}. Meta replaces the whole components list and re-reviews the template."""
    body: dict = {"components": components}
    if category:
        body["category"] = category
    resp = _call("POST", f"{creds.base_url}/{meta_template_id}", creds, json=body)
    if resp.status_code >= 400:
        raise MetaApiError(_error_text(resp), 400 if resp.status_code < 500 else 502)


def delete_template(creds: WhatsAppCredentials, name: str, meta_template_id: str | None) -> None:
    """DELETE /{waba}/message_templates. Pass the id so only this language is removed, not every variant."""
    params = {"name": name}
    if meta_template_id:
        params["hsm_id"] = meta_template_id
    resp = _call("DELETE", f"{creds.base_url}/{creds.waba_id}/message_templates", creds, params=params)
    if resp.status_code == 404:
        return  # already gone on Meta's side
    if resp.status_code >= 400:
        raise MetaApiError(_error_text(resp), 400 if resp.status_code < 500 else 502)


def list_templates(creds: WhatsAppCredentials) -> list[dict]:
    """GET /{waba}/message_templates, following the paging links."""
    url: str | None = f"{creds.base_url}/{creds.waba_id}/message_templates"
    params: dict | None = {"limit": PAGE_LIMIT, "fields": TEMPLATE_FIELDS}
    items: list[dict] = []
    for _ in range(50):  # hard stop: 5,000 templates
        resp = _call("GET", url, creds, params=params)
        if resp.status_code >= 400:
            raise MetaApiError(_error_text(resp), 400 if resp.status_code < 500 else 502)
        data = resp.json()
        items.extend(data.get("data") or [])
        url = (data.get("paging") or {}).get("next")
        params = None  # the next link already carries every parameter
        if not url:
            break
    return items
