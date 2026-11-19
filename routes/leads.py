"""
Leads router — HTTP endpoints for the authenticated Leads module.

ROUTE OVERVIEW:
    GET    /leads                   — paginated list with filters
    POST   /leads                   — create a lead manually
    GET    /leads/stats             — counts by status and source (dashboard widget)
    GET    /leads/{id}              — single lead detail
    PUT    /leads/{id}              — update a lead (partial)
    DELETE /leads/{id}              — soft-delete a lead
    POST   /leads/{id}/convert      — convert lead to a Contact
    POST   /leads/{id}/send-whatsapp — manually trigger WhatsApp welcome message

AUTH:
    Every endpoint requires a valid JWT (Depends(get_current_user)).
    The tenant schema is extracted from the JWT (`current_user._tenant_schema`).
"""

import datetime
from datetime import timezone as _tz
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db, get_session_for_schema
from models.employee import Employee
from schemas.lead import (
    ConvertLeadResponse,
    LeadCreate,
    LeadListResponse,
    LeadResponse,
    LeadStatsResponse,
    LeadUpdate,
)
import services.lead_service as lead_svc
from services.lead_access import is_head
from services.whatsapp_service import send_whatsapp_template

router = APIRouter(
    prefix="/leads",
    tags=["leads"],
    dependencies=[Depends(get_current_user)],
)


@router.get("", response_model=LeadListResponse, summary="List leads")
def list_leads(
    status: str | None = Query(None, description="Filter by status: new/contacted/qualified/converted/lost"),
    source_id: int | None = Query(None, description="Filter by lead_sources.id (1=Walk-in, 3=Meta Ads, 4=Website, 5=WhatsApp…)"),
    assigned_to: int | None = Query(None, description="Filter by assigned employee id"),
    date_from: datetime.date | None = Query(None, description="Created on or after this date"),
    date_to: datetime.date | None = Query(None, description="Created on or before this date"),
    search: str | None = Query(None, description="Search by name, phone, or email"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: Employee = Depends(get_current_user),
):
    """
    Return a paginated list of leads for the current tenant.

    The list is sorted newest-first. Use `skip` and `limit` for pagination.
    """
    schema = current_user._tenant_schema
    with get_session_for_schema(schema) as db:
        return lead_svc.list_leads(
            db,
            status=status,
            source_id=source_id,
            assigned_to=assigned_to,
            date_from=date_from,
            date_to=date_to,
            search=search,
            skip=skip,
            limit=limit,
        )


@router.get("/stats", response_model=LeadStatsResponse, summary="Lead counts by status and source")
def lead_stats(current_user: Employee = Depends(get_current_user)):
    """
    Return lead counts grouped by status and source.
    Used by the dashboard to show a mini lead summary widget.
    """
    schema = current_user._tenant_schema
    with get_session_for_schema(schema) as db:
        return lead_svc.get_lead_stats(db)


@router.get("/{lead_id}", response_model=LeadResponse, summary="Get one lead")
def get_lead(lead_id: int, current_user: Employee = Depends(get_current_user)):
    """Return full detail for a single lead (includes source_name)."""
    schema = current_user._tenant_schema
    with get_session_for_schema(schema) as db:
        return lead_svc.get_lead_detail(db, lead_id)


@router.post("", response_model=LeadResponse, status_code=201, summary="Create a lead manually")
def create_lead(
    body: LeadCreate,
    background_tasks: BackgroundTasks,
    allow_duplicate: bool = Query(False, description="Set true to skip duplicate check"),
    current_user: Employee = Depends(get_current_user),
):
    """
    Create a new lead manually (by a staff member).

    If the phone or email already exists in leads or contacts, a 409 is returned
    with the existing record's id. Set `?allow_duplicate=true` to override (admin/manager only).

    A WhatsApp welcome message is sent automatically in the background if phone is present.
    """
    if allow_duplicate and not is_head(current_user):
        raise HTTPException(status_code=403, detail="Only admin or manager can bypass duplicate check")
    schema = current_user._tenant_schema
    with get_session_for_schema(schema) as db:
        return lead_svc.create_lead(
            db=db,
            schema=schema,
            first_name=body.first_name,
            source_id=body.source_id,
            last_name=body.last_name,
            phone=body.phone,
            email=body.email,
            notes=body.notes,
            address=body.address,
            tags=body.tags,
            assigned_to=body.assigned_to,
            allow_duplicate=allow_duplicate,
            background_tasks=background_tasks,
        )


@router.put("/{lead_id}", response_model=LeadResponse, summary="Update a lead")
def update_lead(
    lead_id: int,
    body: LeadUpdate,
    current_user: Employee = Depends(get_current_user),
):
    """
    Partially update a lead. Only the fields you send are changed.

    Example: send {"status": "contacted"} to move the lead to 'contacted' status
    without touching any other fields.
    """
    schema = current_user._tenant_schema
    with get_session_for_schema(schema) as db:
        updates = body.model_dump(exclude_none=True)
        return lead_svc.update_lead(db, lead_id, **updates)


@router.delete("/{lead_id}", status_code=204, summary="Soft-delete a lead")
def delete_lead(lead_id: int, current_user: Employee = Depends(get_current_user)):
    """
    Soft-delete a lead (sets deleted_at, never hard-deletes).
    The lead is no longer shown in any list but its data is preserved.
    """
    schema = current_user._tenant_schema
    with get_session_for_schema(schema) as db:
        lead_svc.soft_delete_lead(db, lead_id)


@router.post("/{lead_id}/convert", response_model=ConvertLeadResponse, summary="Convert lead to Contact")
def convert_lead(lead_id: int, current_user: Employee = Depends(get_current_user)):
    """
    Convert a lead into a full Contact.

    If a contact with the same phone number already exists, the lead is linked to
    that contact instead of creating a duplicate.

    After conversion, navigate the user to /contacts/{contact_id}.
    """
    schema = current_user._tenant_schema
    with get_session_for_schema(schema) as db:
        result = lead_svc.convert_lead(db, lead_id, current_user.id)
        return result


@router.post("/{lead_id}/send-whatsapp", response_model=dict, summary="Manually send WhatsApp message")
def send_whatsapp(lead_id: int, current_user: Employee = Depends(get_current_user)):
    """
    Manually trigger the WhatsApp welcome template for a lead.

    Useful when the automatic send failed (e.g. the API was temporarily down)
    or when a staff member wants to re-send after updating the phone number.
    """
    schema = current_user._tenant_schema
    with get_session_for_schema(schema) as db:
        lead = lead_svc.get_lead(db, lead_id)
        if not lead.phone:
            return {"success": False, "detail": "Lead has no phone number"}
        sent = send_whatsapp_template(lead.phone, "welcome_lead", [lead.first_name])
        if sent:
            lead.whatsapp_sent = True
            lead.whatsapp_sent_at = datetime.datetime.now(_tz.utc)
            db.commit()
        return {"success": sent}
