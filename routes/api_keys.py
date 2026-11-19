"""
API key management — endpoints for generating and revoking tenant inbound API keys.

WHAT ARE INBOUND API KEYS?
--------------------------
These keys authenticate the public lead inbound endpoints:
  POST /leads/inbound/website/{slug}
  POST /leads/inbound/sheet/{slug}

An admin generates a key here, copies the raw value ONCE, then pastes it into
their website form script or Zapier automation as the X-Api-Key header value.

WHY ADMIN ONLY?
  API keys give external systems the ability to create leads in the CRM.
  Only admins and managers should control what external systems have access.

ROUTE OVERVIEW:
  GET    /api-keys        — list all keys for this tenant (labels only, no hash)
  POST   /api-keys        — generate a new key (raw key shown ONCE)
  DELETE /api-keys/{id}   — revoke (deactivate) a key
"""

import hashlib
import os
import secrets
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from auth import get_current_user, require_admin_or_manager
from database import get_public_db_ctx
from models.employee import Employee
from models.tenant import Tenant
from models.tenant_api_key import TenantApiKey
from schemas.lead import TenantApiKeyCreate, TenantApiKeyCreatedResponse, TenantApiKeyResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api-keys",
    tags=["api-keys"],
    dependencies=[Depends(require_admin_or_manager)],
)


def _get_tenant_id(schema: str, db: Session) -> int:
    """Look up the tenant id in public.tenants by schema_name."""
    tenant = db.scalar(select(Tenant).where(Tenant.schema_name == schema))
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant.id


@router.get("", response_model=list[TenantApiKeyResponse], summary="List API keys")
def list_api_keys(current_user: Employee = Depends(get_current_user)):
    """List all inbound API keys for the current tenant (labels and status only)."""
    schema = current_user._tenant_schema
    with get_public_db_ctx() as db:
        tenant_id = _get_tenant_id(schema, db)
        keys = db.scalars(
            select(TenantApiKey)
            .where(TenantApiKey.tenant_id == tenant_id)
            .order_by(TenantApiKey.created_at.desc())
        ).all()
        return [TenantApiKeyResponse.model_validate(k) for k in keys]


@router.post("", response_model=TenantApiKeyCreatedResponse, status_code=201, summary="Generate a new API key")
def create_api_key(
    body: TenantApiKeyCreate,
    current_user: Employee = Depends(get_current_user),
):
    """
    Generate a new inbound API key.

    The raw key is returned ONCE in this response. It is NOT stored — only its
    SHA-256 hash is kept. Copy it immediately and store it securely in your script.

    HOW SECRETS.TOKEN_HEX WORKS:
        `secrets.token_hex(32)` generates 32 random bytes and returns them as a
        64-character hex string. This is cryptographically random — safe to use
        as a secret key. Each key is unique and unpredictable.
    """
    schema = current_user._tenant_schema
    raw_key = secrets.token_hex(32)  # 64 hex chars = 256 bits of entropy
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    with get_public_db_ctx() as db:
        tenant_id = _get_tenant_id(schema, db)
        key_row = TenantApiKey(
            tenant_id=tenant_id,
            key_hash=key_hash,
            label=body.label,
            is_active=True,
        )
        db.add(key_row)
        db.commit()
        db.refresh(key_row)

    return TenantApiKeyCreatedResponse(
        id=key_row.id,
        label=key_row.label,
        is_active=key_row.is_active,
        created_at=key_row.created_at,
        raw_key=raw_key,
    )


@router.delete("/{key_id}", status_code=204, summary="Revoke an API key")
def revoke_api_key(key_id: int, current_user: Employee = Depends(get_current_user)):
    """
    Revoke (deactivate) an API key.

    The key is not deleted — setting is_active=False means:
      - Existing scripts using it will get 401 immediately.
      - The row is kept for audit purposes (you can see when it was created).
    """
    schema = current_user._tenant_schema
    with get_public_db_ctx() as db:
        tenant_id = _get_tenant_id(schema, db)
        key_row = db.scalar(
            select(TenantApiKey)
            .where(TenantApiKey.id == key_id)
            .where(TenantApiKey.tenant_id == tenant_id)
        )
        if not key_row:
            raise HTTPException(status_code=404, detail="API key not found")
        key_row.is_active = False
        db.commit()
