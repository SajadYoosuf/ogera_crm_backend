"""
TenantApiKey model — API keys used to authenticate public inbound lead endpoints.

WHY THIS EXISTS:
    The normal CRM endpoints require a JWT (an employee must be logged in).
    But inbound lead sources — a website contact form, a Zapier automation, a
    Google Sheet macro — are not run by employees. They are scripts. We issue each
    tenant a secret API key so those scripts can POST leads to our server.

HOW IT WORKS:
    1. An admin visits CRM Settings → API Keys and generates a new key.
    2. The backend creates a random key, hashes it with SHA-256, and stores
       only the hash here (we never store the plaintext key).
    3. The raw key is shown to the admin ONCE — they paste it into their script.
    4. When a script sends a request, we hash the key it provides and compare
       it against the stored hashes. If they match, the request is authorised.

SECURITY NOTE:
    SHA-256 is used (not bcrypt) because API keys are long, random strings — there
    is no dictionary attack risk. SHA-256 is fast enough for real-time verification.

TABLE LOCATION:
    This table lives in the PUBLIC schema (not inside any tenant schema) because
    it must be readable before we know which tenant schema to look in.
"""

import datetime
import os
from typing import Optional

from sqlalchemy import Column, DateTime, func
from sqlmodel import SQLModel, Field

_PUBLIC_SCHEMA = {} if os.getenv("DATABASE_URL", "").startswith("sqlite") else {"schema": "public"}


class TenantApiKey(SQLModel, table=True):
    """One API key record for a tenant's inbound lead endpoint."""

    __tablename__ = "tenant_api_keys"
    __table_args__ = _PUBLIC_SCHEMA

    id: Optional[int] = Field(default=None, primary_key=True)
    # Which tenant this key belongs to.
    # SQLite (tests) creates `tenants` without the "public" schema, so the FK target must match.
    tenant_id: int = Field(foreign_key="tenants.id" if not _PUBLIC_SCHEMA else "public.tenants.id")
    # SHA-256 hex digest of the raw key — 64 characters, never the plaintext key.
    key_hash: str = Field(max_length=64)
    # Human-readable label so the admin can tell keys apart, e.g. "website-form" or "zapier".
    label: str = Field(default="default", max_length=60)
    # Revoke a key by setting is_active=False instead of deleting the row.
    is_active: bool = Field(default=True)
    created_at: datetime.datetime = Field(
        sa_column=Column("created_at", DateTime, nullable=False, server_default=func.now())
    )
