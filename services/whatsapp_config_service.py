"""
WhatsApp connection settings: save, read, test and remove a tenant's Meta credentials.

Secrets (access token, app secret) are encrypted on the way in and are never returned to the
browser. Internal code that needs to call Meta uses get_credentials().
"""
import datetime
import logging
import secrets
from dataclasses import dataclass

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from database import get_public_db_ctx
from models.whatsapp_config import WhatsAppConfig, WhatsAppTenantMap
from schemas.whatsapp_config import WhatsAppConfigResponse, WhatsAppConfigUpdate
from services.crypto import CryptoError, decrypt_secret, encrypt_secret, mask_secret

logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com"
META_TIMEOUT = 15.0


@dataclass
class WhatsAppCredentials:
    phone_number_id: str
    waba_id: str
    app_id: str | None
    api_version: str
    access_token: str
    app_secret: str | None
    verify_token: str

    @property
    def base_url(self) -> str:
        return f"{GRAPH_URL}/{self.api_version}"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def get_config(db: Session) -> WhatsAppConfig | None:
    return db.scalar(select(WhatsAppConfig).limit(1))


def _decrypt_or_500(value: str) -> str:
    try:
        return decrypt_secret(value)
    except CryptoError as exc:
        logger.error("WhatsApp secret could not be decrypted: %s", exc)
        raise HTTPException(status_code=500, detail="WhatsApp credentials cannot be read. Re-enter the access token.")


def get_credentials(db: Session) -> WhatsAppCredentials:
    """Decrypted credentials for server-side Meta calls. 404 if the tenant has not set WhatsApp up."""
    cfg = get_config(db)
    if cfg is None:
        raise HTTPException(status_code=404, detail="WhatsApp is not configured")
    return WhatsAppCredentials(
        phone_number_id=cfg.phone_number_id,
        waba_id=cfg.waba_id,
        app_id=cfg.app_id,
        api_version=cfg.api_version,
        access_token=_decrypt_or_500(cfg.access_token_enc),
        app_secret=_decrypt_or_500(cfg.app_secret_enc) if cfg.app_secret_enc else None,
        verify_token=cfg.verify_token,
    )


def to_response(cfg: WhatsAppConfig | None, webhook_url: str) -> WhatsAppConfigResponse:
    if cfg is None:
        return WhatsAppConfigResponse(configured=False, webhook_url=webhook_url)
    try:
        hint = mask_secret(decrypt_secret(cfg.access_token_enc))
    except CryptoError:
        hint = None
    return WhatsAppConfigResponse(
        configured=True,
        phone_number_id=cfg.phone_number_id,
        waba_id=cfg.waba_id,
        app_id=cfg.app_id,
        business_id=cfg.business_id,
        api_version=cfg.api_version,
        has_access_token=bool(cfg.access_token_enc),
        access_token_hint=hint,
        has_app_secret=bool(cfg.app_secret_enc),
        verify_token=cfg.verify_token,
        webhook_url=webhook_url,
        display_phone_number=cfg.display_phone_number,
        verified_name=cfg.verified_name,
        quality_rating=cfg.quality_rating,
        status=cfg.status,
        connected_at=cfg.connected_at,
        last_checked_at=cfg.last_checked_at,
        last_error=cfg.last_error,
    )


def _new_verify_token() -> str:
    return secrets.token_urlsafe(24)


def _encrypt_or_500(value: str) -> str:
    try:
        return encrypt_secret(value)
    except CryptoError as exc:
        logger.error("WhatsApp secret could not be encrypted: %s", exc)
        raise HTTPException(status_code=500, detail="Server is not set up to store WhatsApp credentials")


def _sync_tenant_map(schema: str, waba_id: str, phone_number_id: str, old_waba_id: str | None) -> None:
    """Point the WABA id at this tenant in public, so inbound webhooks can find the schema."""
    with get_public_db_ctx() as pdb:
        existing = pdb.get(WhatsAppTenantMap, waba_id)
        if existing and existing.schema_name != schema:
            raise HTTPException(status_code=409, detail="This WhatsApp Business Account is already connected to another company")
        clash = pdb.scalar(
            select(WhatsAppTenantMap).where(
                WhatsAppTenantMap.phone_number_id == phone_number_id,
                WhatsAppTenantMap.waba_id != waba_id,
            )
        )
        if clash and clash.schema_name != schema:
            raise HTTPException(status_code=409, detail="This phone number is already connected to another company")
        if old_waba_id and old_waba_id != waba_id:
            pdb.execute(
                delete(WhatsAppTenantMap).where(
                    WhatsAppTenantMap.waba_id == old_waba_id, WhatsAppTenantMap.schema_name == schema
                )
            )
        if clash and clash.waba_id != old_waba_id:  # same tenant, stale row for the same number
            pdb.delete(clash)
            pdb.flush()
        if existing:
            existing.phone_number_id = phone_number_id
        else:
            pdb.add(WhatsAppTenantMap(waba_id=waba_id, phone_number_id=phone_number_id, schema_name=schema))
        pdb.commit()


def _remove_tenant_map(schema: str) -> None:
    with get_public_db_ctx() as pdb:
        pdb.execute(delete(WhatsAppTenantMap).where(WhatsAppTenantMap.schema_name == schema))
        pdb.commit()


def save_config(db: Session, schema: str, data: WhatsAppConfigUpdate, employee_id: int) -> WhatsAppConfig:
    cfg = get_config(db)
    if cfg is None and not data.access_token:
        raise HTTPException(status_code=422, detail="Enter the access token")

    old_waba = cfg.waba_id if cfg else None
    _sync_tenant_map(schema, data.waba_id, data.phone_number_id, old_waba)

    if cfg is None:
        cfg = WhatsAppConfig(verify_token=_new_verify_token(), status="disconnected")
        db.add(cfg)

    # Changing the account or the token invalidates what "Test connection" last said.
    changed = (
        cfg.phone_number_id != data.phone_number_id
        or cfg.waba_id != data.waba_id
        or data.access_token is not None
    )
    cfg.phone_number_id = data.phone_number_id
    cfg.waba_id = data.waba_id
    cfg.app_id = data.app_id
    cfg.business_id = data.business_id
    cfg.api_version = data.api_version
    if data.access_token:
        cfg.access_token_enc = _encrypt_or_500(data.access_token)
    if data.app_secret:
        cfg.app_secret_enc = _encrypt_or_500(data.app_secret)
    if changed:
        cfg.status = "disconnected"
        cfg.connected_at = None
        cfg.display_phone_number = cfg.verified_name = cfg.quality_rating = None
        cfg.last_error = None
    cfg.updated_by = employee_id
    db.commit()
    db.refresh(cfg)
    return cfg


def rotate_verify_token(db: Session) -> WhatsAppConfig:
    cfg = get_config(db)
    if cfg is None:
        raise HTTPException(status_code=404, detail="WhatsApp is not configured")
    cfg.verify_token = _new_verify_token()
    db.commit()
    db.refresh(cfg)
    return cfg


def delete_config(db: Session, schema: str) -> None:
    cfg = get_config(db)
    if cfg is not None:
        db.delete(cfg)
        db.commit()
    _remove_tenant_map(schema)


def _meta_error(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", {})
        return err.get("message") or f"Meta returned HTTP {resp.status_code}"
    except ValueError:
        return f"Meta returned HTTP {resp.status_code}"


def _record(db: Session, cfg: WhatsAppConfig, ok: bool, detail: str) -> None:
    cfg.last_checked_at = _now()
    if ok:
        cfg.status = "connected"
        cfg.last_error = None
        if cfg.connected_at is None:
            cfg.connected_at = _now()
    else:
        cfg.status = "error"
        cfg.last_error = detail[:500]
    db.commit()
    db.refresh(cfg)


def test_connection(db: Session) -> tuple[bool, str, WhatsAppConfig]:
    """Ask Meta about the phone number and the WABA, and store what it says."""
    cfg = get_config(db)
    if cfg is None:
        raise HTTPException(status_code=404, detail="WhatsApp is not configured")
    creds = get_credentials(db)
    headers = {"Authorization": f"Bearer {creds.access_token}"}
    try:
        with httpx.Client(timeout=META_TIMEOUT) as client:
            phone = client.get(
                f"{creds.base_url}/{creds.phone_number_id}",
                params={"fields": "display_phone_number,verified_name,quality_rating"},
                headers=headers,
            )
            if phone.status_code != 200:
                detail = f"Phone number check failed: {_meta_error(phone)}"
                _record(db, cfg, False, detail)
                return False, detail, cfg
            waba = client.get(f"{creds.base_url}/{creds.waba_id}", params={"fields": "id,name"}, headers=headers)
            if waba.status_code != 200:
                detail = f"WhatsApp Business Account check failed: {_meta_error(waba)}"
                _record(db, cfg, False, detail)
                return False, detail, cfg
    except httpx.HTTPError as exc:
        logger.warning("WhatsApp test connection could not reach Meta: %s", exc)
        detail = "Could not reach Meta. Try again in a moment."
        _record(db, cfg, False, detail)
        return False, detail, cfg

    info = phone.json()
    cfg.display_phone_number = info.get("display_phone_number")
    cfg.verified_name = info.get("verified_name")
    cfg.quality_rating = info.get("quality_rating")
    _record(db, cfg, True, "")
    return True, "Connected", cfg


def subscribe_app(db: Session) -> tuple[bool, str]:
    """Subscribe the Meta app to this WABA so webhook events (messages, template status) are sent."""
    creds = get_credentials(db)
    try:
        with httpx.Client(timeout=META_TIMEOUT) as client:
            resp = client.post(
                f"{creds.base_url}/{creds.waba_id}/subscribed_apps",
                headers={"Authorization": f"Bearer {creds.access_token}"},
            )
    except httpx.HTTPError as exc:
        logger.warning("WhatsApp subscribe could not reach Meta: %s", exc)
        return False, "Could not reach Meta. Try again in a moment."
    if resp.status_code != 200:
        return False, _meta_error(resp)
    return True, "Subscribed"
