"""
Lead inbound router — PUBLIC endpoints for receiving leads from external sources.

WHAT ARE THESE ENDPOINTS?
--------------------------
These endpoints have NO JWT authentication. They are designed to be called by
external scripts that don't have an employee login:
  - A website contact form
  - A Zapier automation reading a Google Sheet with Meta Ads leads
  - Any other automation tool

Instead of JWT, they authenticate using an API key:
  - The tenant admin generates an API key in CRM Settings.
  - They embed it in their script's HTTP request header: X-Api-Key: <key>
  - Our backend hashes the key and checks it against public.tenant_api_keys.

The URL also includes the tenant_slug (which is the tenant's schema_name),
so we can open the correct tenant's database schema.

HOW TENANT_SLUG IDENTIFIES THE TENANT:
  public.tenants has a `schema_name` column, e.g. "ventino", "acme_corp".
  The slug in the URL IS that schema_name.
  So /leads/inbound/ventino routes to the ventino schema's leads table.

ENDPOINTS:
  POST /leads/inbound/website/{tenant_slug}  — website form
  POST /leads/inbound/sheet/{tenant_slug}    — Google Sheet / Meta Ads via Zapier
"""

import hashlib
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from database import get_public_db_ctx, get_session_for_schema
from models.tenant import Tenant
from models.tenant_api_key import TenantApiKey
import services.lead_service as lead_svc
from schemas.lead import InboundLeadResponse, SheetLeadCreate, WebsiteLeadCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leads/inbound", tags=["leads-inbound"])


# ── Auth helper ───────────────────────────────────────────────────────────────

def _get_schema_for_slug(
    tenant_slug: str,
    api_key: str | None,
    db_public: Session,
) -> str:
    """
    Identify and authorise the tenant from the slug + API key.

    HOW IT WORKS:
      1. Look up the tenant in public.tenants by schema_name = tenant_slug.
      2. Hash the provided api_key with SHA-256.
      3. Check if that hash exists in public.tenant_api_keys for this tenant.
      4. If matched, return the schema_name for use in get_session_for_schema().

    WHY SHA-256 AND NOT BCRYPT?
      API keys are long, random strings — there is no dictionary of "likely" keys to try.
      SHA-256 is fast (sub-millisecond) and appropriate for high-entropy random values.
      Bcrypt would add ~200ms latency per request with no real security benefit here.

    Returns:
      schema name (str) if auth succeeds
    Raises:
      HTTPException(401) if tenant not found, key missing, or key invalid
    """
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="API key required. Pass X-Api-Key header or ?api_key= query parameter.",
        )

    # 1. Find the tenant
    tenant = db_public.scalar(
        select(Tenant)
        .where(Tenant.schema_name == tenant_slug)
        .where(Tenant.status == "active")
    )
    if not tenant:
        # Return 401 (not 404) — don't reveal whether a slug exists
        raise HTTPException(status_code=401, detail="Invalid tenant or API key")

    # 2. Hash the provided key
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    # 3. Check the hash against the stored keys for this tenant
    match = db_public.scalar(
        select(TenantApiKey)
        .where(TenantApiKey.tenant_id == tenant.id)
        .where(TenantApiKey.key_hash == key_hash)
        .where(TenantApiKey.is_active.is_(True))
    )
    if not match:
        raise HTTPException(status_code=401, detail="Invalid tenant or API key")

    return tenant.schema_name


# ── Website form endpoint ─────────────────────────────────────────────────────

@router.post(
    "/website/{tenant_slug}",
    response_model=InboundLeadResponse,
    status_code=201,
    summary="Receive a lead from a website contact form",
)
def inbound_website(
    tenant_slug: str = Path(description="Your CRM tenant ID (provided by admin)"),
    body: WebsiteLeadCreate = ...,
    background_tasks: BackgroundTasks = ...,
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
):
    """
    Create a lead from a website contact form submission.

    HOW TO USE FROM A WEBSITE FORM:
        Your form's action URL: POST https://api.yourdomain.com/leads/inbound/website/{your_slug}
        Add a hidden field: X-Api-Key header or ?api_key= query parameter with your tenant API key.

    CORS NOTE:
        If embedding as a browser fetch(), add your website origin to CORS_ORIGINS in config.py.
        If using a server-side form handler (PHP, Node.js), CORS is not relevant.

    EMBED EXAMPLE (HTML form with fetch):
        fetch('/leads/inbound/website/acme_corp?api_key=YOUR_KEY', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({first_name: ..., phone: ..., email: ...})
        })

    EXTRA FIELDS:
        Any extra fields you send (utm_source, page_url, form_name, etc.) are stored
        in the lead's raw_data column so nothing is lost.
    """
    try:
        with get_public_db_ctx() as db_public:
            schema = _get_schema_for_slug(tenant_slug, x_api_key, db_public)
    except OperationalError as exc:
        logger.error("inbound_website — public DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")

    # Build raw_payload from any extra fields the form included
    raw_payload = {
        "source": "website_form",
        "tenant_slug": tenant_slug,
        **body.model_extra,
    }

    try:
        with get_session_for_schema(schema) as db:
            lead = lead_svc.create_lead(
                db=db,
                schema=schema,
                first_name=body.first_name,
                source_id=4,  # Website
                last_name=body.last_name,
                phone=body.phone,
                email=body.email,
                notes=body.notes,
                raw_payload=raw_payload,
                background_tasks=background_tasks,
            )
            return InboundLeadResponse(id=lead.id, status="created")
    except OperationalError as exc:
        logger.error("inbound_website — tenant DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")


# ── Google Sheet / Meta Ads endpoint ─────────────────────────────────────────

@router.post(
    "/sheet/{tenant_slug}",
    response_model=InboundLeadResponse,
    status_code=201,
    summary="Receive a lead from a Google Sheet (Meta Ads / Zapier)",
)
def inbound_sheet(
    tenant_slug: str = Path(description="Your CRM tenant ID"),
    body: SheetLeadCreate = ...,
    background_tasks: BackgroundTasks = ...,
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
):
    """
    Create a lead from a Meta Ads Google Sheet via Zapier or Google Apps Script.

    HOW TO SET UP WITH ZAPIER:
        Trigger: Google Sheets → New Spreadsheet Row
        Action: Webhooks by Zapier → POST
            URL: https://api.yourdomain.com/leads/inbound/sheet/{your_slug}
            Headers: X-Api-Key: {your_tenant_api_key}
            Payload: map sheet columns to first_name, last_name, phone, email, ad_name, form_name

    HOW TO SET UP WITH GOOGLE APPS SCRIPT:
        In your Sheet: Extensions → Apps Script → paste the following:
        ```
        function onEdit(e) {
          var row = e.range.getRow();
          var sheet = e.source.getActiveSheet();
          var payload = {
            first_name: sheet.getRange(row, 1).getValue(),
            phone: sheet.getRange(row, 2).getValue(),
            ad_name: sheet.getRange(row, 3).getValue()
          };
          UrlFetchApp.fetch(
            'https://api.yourdomain.com/leads/inbound/sheet/acme_corp?api_key=YOUR_KEY',
            {method:'post', contentType:'application/json', payload: JSON.stringify(payload)}
          );
        }
        ```
    """
    try:
        with get_public_db_ctx() as db_public:
            schema = _get_schema_for_slug(tenant_slug, x_api_key, db_public)
    except OperationalError as exc:
        logger.error("inbound_sheet — public DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")

    raw_payload = {
        "source": "meta_ads_sheet",
        "tenant_slug": tenant_slug,
        "ad_name": body.ad_name,
        "form_name": body.form_name,
        "campaign_id": body.campaign_id,
        "ad_set_id": body.ad_set_id,
        **body.model_extra,
    }

    # Build notes from ad context if no notes provided
    notes = body.notes
    if not notes and body.ad_name:
        notes = f"Meta Ads: {body.ad_name}"

    try:
        with get_session_for_schema(schema) as db:
            lead = lead_svc.create_lead(
                db=db,
                schema=schema,
                first_name=body.first_name,
                source_id=3,  # Meta Ads
                last_name=body.last_name,
                phone=body.phone,
                email=body.email,
                notes=notes,
                raw_payload=raw_payload,
                background_tasks=background_tasks,
            )
            return InboundLeadResponse(id=lead.id, status="created")
    except OperationalError as exc:
        logger.error("inbound_sheet — tenant DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")
