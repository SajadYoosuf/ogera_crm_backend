"""
WhatsApp inbox router — the cross-lead view behind the sidebar "WhatsApp" page.

ENDPOINTS:
    GET  /whatsapp-inbox/conversations                    — one row per lead that has WhatsApp messages
    GET  /whatsapp-inbox/unread-count                     — conversations with unread inbound messages (sidebar badge)
    POST /whatsapp-inbox/conversations/{lead_id}/mark-read — mark a lead's inbound messages as read
    POST /whatsapp-inbox/conversations/{lead_id}/assign    — head only: give the lead to an employee

Messages of one conversation are read and sent through the existing per-lead routes
(routes/lead_whatsapp.py), so access rules stay in one place.

ACCESS (same as leads): heads (roles.see_all_records) see every conversation;
everyone else only sees leads assigned to them.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import OperationalError

from database import get_session_for_schema
from models.employee import Employee
from models.lead import Lead
from models.whatsapp_message import WhatsAppMessage
import services.lead_service as lead_svc
from services.lead_access import ensure_can_work_lead, ensure_is_head, is_head
from services.permissions import require_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp-inbox", tags=["whatsapp-inbox"])

_DB_DOWN = "Server unavailable, please retry in a moment."


class AssignBody(BaseModel):
    employee_id: int


def _visible_leads(current_user: Employee):
    """Leads the user may see in the inbox (not deleted; staff only their own)."""
    q = select(Lead.id).where(Lead.deleted_at.is_(None))
    if not is_head(current_user):
        q = q.where(Lead.assigned_to == current_user.id)
    return q


def _unread_by_lead():
    return (
        select(WhatsAppMessage.lead_id, func.count(WhatsAppMessage.id).label("unread"))
        .where(WhatsAppMessage.direction == "in", WhatsAppMessage.is_read.is_(False))
        .group_by(WhatsAppMessage.lead_id)
        .subquery()
    )


@router.get("/conversations", summary="List WhatsApp conversations")
def list_conversations(
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    search: str | None = Query(None, max_length=100),
    current_user: Employee = Depends(require_permission("leads", "r")),
):
    try:
        with get_session_for_schema(current_user._tenant_schema) as db:
            last_sq = (
                select(WhatsAppMessage.lead_id, func.max(WhatsAppMessage.id).label("last_id"))
                .group_by(WhatsAppMessage.lead_id)
                .subquery()
            )
            unread_sq = _unread_by_lead()

            base = (
                select(Lead, WhatsAppMessage, func.coalesce(unread_sq.c.unread, 0))
                .join(last_sq, Lead.id == last_sq.c.lead_id)
                .join(WhatsAppMessage, WhatsAppMessage.id == last_sq.c.last_id)
                .outerjoin(unread_sq, unread_sq.c.lead_id == Lead.id)
                .where(Lead.id.in_(_visible_leads(current_user)))
            )
            if search and search.strip():
                term = f"%{search.strip()}%"
                base = base.where(or_(
                    Lead.first_name.ilike(term), Lead.last_name.ilike(term), Lead.phone.ilike(term),
                ))

            total = db.scalar(select(func.count()).select_from(base.with_only_columns(Lead.id).subquery())) or 0
            rows = db.execute(
                base.order_by(WhatsAppMessage.created_at.desc(), WhatsAppMessage.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()

            assignee_ids = {lead.assigned_to for lead, _, _ in rows if lead.assigned_to}
            names = {}
            if assignee_ids:
                names = {
                    e.id: e.name
                    for e in db.scalars(select(Employee).where(Employee.id.in_(assignee_ids))).all()
                }

            items = [
                {
                    "lead_id": lead.id,
                    "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip() or lead.phone or "Unknown",
                    "phone": lead.phone,
                    "status": lead.status,
                    "last_message": {
                        "body": msg.body,
                        "direction": msg.direction,
                        "content_type": msg.content_type,
                        "template_name": msg.template_name,
                        "created_at": msg.created_at,
                    },
                    "unread_count": int(unread),
                    "assigned_to": lead.assigned_to,
                    "assigned_name": names.get(lead.assigned_to),
                }
                for lead, msg, unread in rows
            ]
            return {"items": items, "total": total, "page": page, "page_size": page_size}
    except OperationalError as exc:
        logger.error("list_conversations — DB error: %s", exc)
        raise HTTPException(503, detail=_DB_DOWN)


@router.get("/unread-count", summary="Conversations with unread messages")
def unread_count(current_user: Employee = Depends(require_permission("leads", "r"))):
    try:
        with get_session_for_schema(current_user._tenant_schema) as db:
            count = db.scalar(
                select(func.count(func.distinct(WhatsAppMessage.lead_id)))
                .where(
                    WhatsAppMessage.direction == "in",
                    WhatsAppMessage.status != "read",
                    WhatsAppMessage.lead_id.in_(_visible_leads(current_user)),
                )
            ) or 0
            return {"count": int(count)}
    except OperationalError as exc:
        logger.error("unread_count — DB error: %s", exc)
        raise HTTPException(503, detail=_DB_DOWN)


@router.post("/conversations/{lead_id}/mark-read", summary="Mark a conversation as read")
def mark_read(lead_id: int, current_user: Employee = Depends(require_permission("leads", "r"))):
    try:
        with get_session_for_schema(current_user._tenant_schema) as db:
            lead = lead_svc.get_lead(db, lead_id)
            ensure_can_work_lead(current_user, lead)
            db.execute(
                update(WhatsAppMessage)
                .where(
                    WhatsAppMessage.lead_id == lead_id,
                    WhatsAppMessage.direction == "in",
                    WhatsAppMessage.status != "read",
                )
                .values(status="read")
            )
            db.commit()
            return {"ok": True}
    except OperationalError as exc:
        logger.error("mark_read — DB error: %s", exc)
        raise HTTPException(503, detail=_DB_DOWN)


@router.post("/conversations/{lead_id}/assign", summary="Assign a conversation's lead to an employee (admin/manager)")
def assign_conversation(
    lead_id: int,
    body: AssignBody,
    current_user: Employee = Depends(require_permission("leads", "r")),
):
    ensure_is_head(current_user, "assign conversations")
    try:
        with get_session_for_schema(current_user._tenant_schema) as db:
            lead = lead_svc.get_lead(db, lead_id)
            assignee = db.get(Employee, body.employee_id)
            if not assignee or assignee.deleted_at is not None or not assignee.is_active:
                raise HTTPException(400, detail="That employee is not available for assignment")
            previous = lead.assigned_to
            updated = lead_svc.update_lead(db, lead_id, actor_id=current_user.id, assigned_to=body.employee_id)
            if updated.assigned_to != previous:
                lead_svc.notify_lead_assigned(db, updated, current_user.id)
                db.commit()
            return {"ok": True}
    except OperationalError as exc:
        logger.error("assign_conversation — DB error: %s", exc)
        raise HTTPException(503, detail=_DB_DOWN)
