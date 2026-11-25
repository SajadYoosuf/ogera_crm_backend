"""
Template management: create, edit, submit to Meta, sync from Meta, delete, and apply Meta's webhook updates.

A template is a DRAFT (local only) until it is submitted. After that Meta decides its status and the
local row follows: through the webhook when it is set up, or the Sync button when it is not.

Setting WHATSAPP_TEMPLATES_DRY_RUN=1 skips every Meta call and pretends it succeeded, so the screens
can be tried without a Meta app.
"""
import datetime
import logging
import os
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.whatsapp_template import WhatsAppTemplate
from schemas.whatsapp_template import (
    TemplateSyncResult,
    WhatsAppTemplateInput,
    WhatsAppTemplateResponse,
)
from services import whatsapp_meta_templates as meta
from services import whatsapp_template_rules as rules
from services.whatsapp_config_service import WhatsAppCredentials, get_config, get_credentials

logger = logging.getLogger(__name__)

SUBMITS_PER_HOUR = 100  # Meta's limit per WhatsApp Business Account


def _dry_run() -> bool:
    return os.getenv("WHATSAPP_TEMPLATES_DRY_RUN", "").lower() in ("1", "true")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# ── Reading ────────────────────────────────────────────────────────────────────

def to_response(t: WhatsAppTemplate) -> WhatsAppTemplateResponse:
    resp = WhatsAppTemplateResponse.model_validate(t)
    resp.body_variable_count = len(rules.extract_variable_indices(t.body_text))
    resp.header_variable_count = len(rules.extract_variable_indices(t.header_content)) if t.header_type == "text" else 0
    resp.can_edit = rules.can_edit(t.status)
    resp.can_submit = t.meta_template_id is None
    return resp


def get_or_404(db: Session, template_id: int) -> WhatsAppTemplate:
    t = db.get(WhatsAppTemplate, template_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return t


def list_templates(db: Session, status: str | None, search: str | None) -> list[WhatsAppTemplate]:
    query = select(WhatsAppTemplate).order_by(WhatsAppTemplate.updated_at.desc(), WhatsAppTemplate.id.desc())
    if status:
        query = query.where(WhatsAppTemplate.status == status.upper())
    if search:
        query = query.where(WhatsAppTemplate.name.ilike(f"%{search.strip().lower()}%"))
    return list(db.scalars(query))


def _input_from_row(t: WhatsAppTemplate) -> WhatsAppTemplateInput:
    return WhatsAppTemplateInput(
        name=t.name, language=t.language, category=t.category, header_type=t.header_type,
        header_content=t.header_content, header_media_url=t.header_media_url, header_handle=t.header_handle,
        body_text=t.body_text, footer_text=t.footer_text, buttons=t.buttons, sample_values=t.sample_values,
    )


# ── Writing ────────────────────────────────────────────────────────────────────

def _validate(data: WhatsAppTemplateInput) -> None:
    try:
        rules.validate_template(data)
    except rules.TemplateRuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _apply(t: WhatsAppTemplate, data: WhatsAppTemplateInput) -> None:
    t.name, t.language, t.category = data.name, data.language, data.category
    t.header_type = data.header_type
    t.header_content = data.header_content if data.header_type == "text" else None
    t.header_media_url = data.header_media_url if data.header_type not in (None, "text") else None
    t.header_handle = data.header_handle if data.header_type not in (None, "text") else None
    t.body_text = data.body_text
    t.footer_text = data.footer_text
    t.buttons = [b.model_dump(exclude_none=True) for b in data.buttons] if data.buttons else None
    t.sample_values = data.sample_values.model_dump() if data.sample_values and (data.sample_values.body or data.sample_values.header) else None


def _commit_unique(db: Session, name: str, language: str) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f'A template named "{name}" in {language} already exists')


def create_draft(db: Session, data: WhatsAppTemplateInput, employee_id: int) -> WhatsAppTemplate:
    _validate(data)
    t = WhatsAppTemplate(status="DRAFT", created_by=employee_id)
    _apply(t, data)
    db.add(t)
    _commit_unique(db, data.name, data.language)
    db.refresh(t)
    return t


def _credentials_or_409(db: Session) -> WhatsAppCredentials:
    if get_config(db) is None:
        raise HTTPException(status_code=409, detail="Connect WhatsApp in Settings before sending templates to Meta")
    return get_credentials(db)


def _meta_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except meta.MetaApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


def update_template(db: Session, t: WhatsAppTemplate, data: WhatsAppTemplateInput) -> WhatsAppTemplate:
    if not rules.can_edit(t.status):
        raise HTTPException(status_code=409, detail="This template cannot be edited while it is " + t.status.replace("_", " ").lower())
    _validate(data)

    if t.meta_template_id is None:  # a draft: only local
        _apply(t, data)
        _commit_unique(db, data.name, data.language)
        db.refresh(t)
        return t

    if (data.name, data.language) != (t.name, t.language):
        raise HTTPException(status_code=422, detail="The name and language of a submitted template cannot be changed. Create a new template instead.")
    if data.category == "AUTHENTICATION":
        raise HTTPException(status_code=422, detail="Authentication templates are not supported yet.")

    if not _dry_run():
        creds = _credentials_or_409(db)
        try:
            meta.edit_template(creds, t.meta_template_id, rules.build_meta_components(data),
                               data.category if data.category != t.category else None)
        except meta.MetaApiError as exc:
            t.submission_error = exc.message[:500]
            db.commit()
            raise HTTPException(status_code=exc.status_code, detail=exc.message)
    _apply(t, data)
    t.status, t.rejection_reason, t.submission_error, t.last_submitted_at = "PENDING", None, None, _now()
    db.commit()
    db.refresh(t)
    return t


def _check_rate_limit(db: Session) -> None:
    since = _now() - datetime.timedelta(hours=1)
    used = db.scalar(select(func.count()).select_from(WhatsAppTemplate).where(WhatsAppTemplate.last_submitted_at >= since)) or 0
    if used >= SUBMITS_PER_HOUR:
        raise HTTPException(status_code=429, detail="Meta allows 100 new templates per hour. Wait a while and try again.")


def submit_template(db: Session, t: WhatsAppTemplate) -> WhatsAppTemplate:
    if t.meta_template_id is not None:
        raise HTTPException(status_code=409, detail="This template was already sent to Meta. Edit it instead.")
    data = _input_from_row(t)
    _validate(data)
    if t.category == "AUTHENTICATION":
        raise HTTPException(status_code=422, detail="Authentication templates are not supported yet.")

    if _dry_run():
        result = {"id": f"dry-run-{uuid.uuid4()}", "status": "PENDING", "category": None}
    else:
        creds = _credentials_or_409(db)
        _check_rate_limit(db)
        try:
            result = meta.submit_template(creds, rules.build_meta_submit_payload(data))
        except meta.MetaApiError as exc:
            t.submission_error = exc.message[:500]
            t.last_submitted_at = _now()
            db.commit()
            raise HTTPException(status_code=exc.status_code, detail=exc.message)

    t.meta_template_id = result["id"][:50]
    t.status = rules.normalize_status(result["status"])
    if result.get("category") in ("MARKETING", "UTILITY"):
        t.category = result["category"]  # Meta may re-categorise a template on submit
    t.rejection_reason, t.submission_error, t.last_submitted_at = None, None, _now()
    db.commit()
    db.refresh(t)
    return t


def delete_template(db: Session, t: WhatsAppTemplate) -> None:
    if t.meta_template_id and not _dry_run() and not t.meta_template_id.startswith("dry-run-"):
        creds = _credentials_or_409(db)
        _meta_call(meta.delete_template, creds, t.name, t.meta_template_id)
    db.delete(t)
    db.commit()


# ── Sync from Meta ─────────────────────────────────────────────────────────────

def sync_from_meta(db: Session, employee_id: int) -> TemplateSyncResult:
    """Pull every template from Meta. Matches on (name, language); drafts that Meta does not know are left alone."""
    if _dry_run():
        return TemplateSyncResult(fetched=0, created=0, updated=0)
    creds = _credentials_or_409(db)
    remote = _meta_call(meta.list_templates, creds)

    existing = {(t.name, t.language): t for t in db.scalars(select(WhatsAppTemplate))}
    created = updated = 0
    for r in remote:
        name, language = r.get("name"), r.get("language")
        if not name or not language:
            continue
        fields = rules.parse_meta_components(r.get("components"))
        category = r.get("category") if r.get("category") in ("MARKETING", "UTILITY", "AUTHENTICATION") else "MARKETING"
        status = rules.normalize_status(r.get("status"))
        row = existing.get((name, language))
        if row is None:
            row = WhatsAppTemplate(name=name, language=language, created_by=employee_id)
            db.add(row)
            existing[(name, language)] = row
            created += 1
        else:
            updated += 1
        for key, value in fields.items():
            setattr(row, key, value)
        row.category, row.status = category, status
        row.meta_template_id = str(r["id"])[:50] if r.get("id") else row.meta_template_id
        row.quality_score = rules.normalize_quality(r.get("quality_score"))
        if status in ("APPROVED", "PENDING"):
            row.rejection_reason = None
        row.submission_error = None
    db.commit()
    return TemplateSyncResult(fetched=len(remote), created=created, updated=updated)


# ── Meta webhook ───────────────────────────────────────────────────────────────

def apply_webhook_change(db: Session, field: str, value: dict) -> bool:
    """Apply one `message_template_*` webhook change. Returns True when a row was changed or created."""
    meta_id = str(value.get("message_template_id") or "") or None
    name = value.get("message_template_name")
    language = value.get("message_template_language")

    row = None
    if meta_id:
        row = db.scalar(select(WhatsAppTemplate).where(WhatsAppTemplate.meta_template_id == meta_id))
    if row is None and name and language:
        row = db.scalar(select(WhatsAppTemplate).where(WhatsAppTemplate.name == name, WhatsAppTemplate.language == language))

    if field == "message_template_status_update":
        status = rules.status_from_webhook_event(value.get("event"))
        if status is None:
            logger.info("Template webhook: ignoring unknown event %r", value.get("event"))
            return False
        if row is None:
            if not (meta_id and name and language):
                return False
            # made directly in Meta Business Manager: keep a stub until the next Sync fills in the content
            row = WhatsAppTemplate(name=name, language=language, body_text="", meta_template_id=meta_id[:50], status=status)
            db.add(row)
        row.status = status
        if meta_id and not row.meta_template_id:
            row.meta_template_id = meta_id[:50]
        reason = value.get("reason")
        if status == "REJECTED":
            row.rejection_reason = (reason if reason and reason.upper() != "NONE" else "Rejected by Meta")[:1000]
        elif status in ("APPROVED", "PENDING"):
            row.rejection_reason = None
        db.commit()
        return True

    if field == "message_template_quality_update":
        score = rules.normalize_quality(value.get("new_quality_score"))
        if row is None or score is None:
            return False
        row.quality_score = score
        db.commit()
        return True

    # message_template_components_update: Meta changed the wording itself. The next Sync picks it up.
    return False
