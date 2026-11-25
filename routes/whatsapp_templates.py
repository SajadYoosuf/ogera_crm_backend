"""
WhatsApp template router (Settings → WhatsApp Templates).

  GET    /whatsapp/templates               — list (filter by status, search by name)
  GET    /whatsapp/templates/{id}          — one template
  POST   /whatsapp/templates               — save a draft (nothing is sent to Meta)
  PUT    /whatsapp/templates/{id}          — edit; a submitted template goes back to Meta for review
  POST   /whatsapp/templates/{id}/submit   — send a draft to Meta for approval
  POST   /whatsapp/templates/sync          — pull every template and its status from Meta
  DELETE /whatsapp/templates/{id}          — delete here and on Meta

Same access as the connection settings: the "integrations" permission (r to view, u to change).
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from auth import get_tenant_db
from models.employee import Employee
from schemas.whatsapp_template import (
    TemplateSyncResult,
    WhatsAppTemplateInput,
    WhatsAppTemplateList,
    WhatsAppTemplateResponse,
)
from services import whatsapp_template_service as svc
from services.permissions import require_permission

router = APIRouter(prefix="/whatsapp/templates", tags=["whatsapp-templates"])

view_templates = require_permission("integrations", "r")
manage_templates = require_permission("integrations", "u")


@router.get("", response_model=WhatsAppTemplateList, summary="List WhatsApp templates")
def list_templates(
    status: str | None = Query(None, description="Only this status, e.g. APPROVED"),
    search: str | None = Query(None, max_length=100),
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(view_templates),
):
    rows = svc.list_templates(db, status, search)
    return WhatsAppTemplateList(items=[svc.to_response(r) for r in rows], total=len(rows))


# Fixed paths go before /{template_id} so "sync" is never read as an id.
@router.post("/sync", response_model=TemplateSyncResult, summary="Sync templates from Meta")
def sync_templates(db: Session = Depends(get_tenant_db), current_user: Employee = Depends(manage_templates)):
    return svc.sync_from_meta(db, current_user.id)


@router.get("/{template_id}", response_model=WhatsAppTemplateResponse, summary="Get one template")
def get_template(template_id: int, db: Session = Depends(get_tenant_db), _: Employee = Depends(view_templates)):
    return svc.to_response(svc.get_or_404(db, template_id))


@router.post("", response_model=WhatsAppTemplateResponse, status_code=201, summary="Save a template draft")
def create_template(
    body: WhatsAppTemplateInput,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(manage_templates),
):
    return svc.to_response(svc.create_draft(db, body, current_user.id))


@router.put("/{template_id}", response_model=WhatsAppTemplateResponse, summary="Edit a template")
def update_template(
    template_id: int,
    body: WhatsAppTemplateInput,
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(manage_templates),
):
    return svc.to_response(svc.update_template(db, svc.get_or_404(db, template_id), body))


@router.post("/{template_id}/submit", response_model=WhatsAppTemplateResponse, summary="Send a draft to Meta for approval")
def submit_template(template_id: int, db: Session = Depends(get_tenant_db), _: Employee = Depends(manage_templates)):
    return svc.to_response(svc.submit_template(db, svc.get_or_404(db, template_id)))


@router.delete("/{template_id}", status_code=204, summary="Delete a template")
def delete_template(template_id: int, db: Session = Depends(get_tenant_db), _: Employee = Depends(manage_templates)):
    svc.delete_template(db, svc.get_or_404(db, template_id))
