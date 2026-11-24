"""
Notification routes — the in-app notification inbox API.

ENDPOINTS (the first three keep the original contract the web/mobile apps use):
    GET    /notifications               — list my notifications, unread first; returns unread_count
    POST   /notifications/read-all      — mark all as read
    POST   /notifications/{id}/read     — mark one as read
    DELETE /notifications/{id}          — dismiss one notification
    POST   /notifications/purge         — delete notifications older than 30 days (read ones)

SECURITY:
    Every endpoint requires a valid JWT. Users only ever touch their OWN rows —
    recipient_id is always compared to the current user's id. The session is the
    tenant-scoped one (get_tenant_db), so rows live in the tenant schema.
"""
import datetime
from datetime import timezone as _tz

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import get_current_user, get_tenant_db
from models import Employee
from models.notification import Notification
from schemas.notification import NotificationListOut

PURGE_DAYS = 30


class OkResponse(BaseModel):
    ok: bool = True


class PurgeResponse(BaseModel):
    deleted: int


router = APIRouter(
    prefix="/notifications",
    tags=["notifications"],
    dependencies=[Depends(get_current_user)],
)


@router.get("", response_model=NotificationListOut, summary="List my notifications")
def list_notifications(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    """Unread first (newest first), then read ones. The panel shows up to 50."""
    base_q = db.query(Notification).filter(Notification.recipient_id == current_user.id)
    unread_count = base_q.filter(Notification.is_read == False).count()  # noqa: E712
    items = (
        base_q
        .order_by(Notification.is_read.asc(), Notification.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return NotificationListOut(items=items, unread_count=unread_count)


@router.post("/read-all", response_model=OkResponse, summary="Mark all notifications as read")
def mark_all_read(
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    db.query(Notification).filter(
        Notification.recipient_id == current_user.id,
        Notification.is_read == False,  # noqa: E712
    ).update({"is_read": True})
    db.commit()
    return {"ok": True}


@router.post("/purge", response_model=PurgeResponse, summary="Delete notifications older than 30 days")
def purge_old(
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    """Removes this user's read notifications older than 30 days. The web panel calls it on open."""
    cutoff = datetime.datetime.now(_tz.utc).replace(tzinfo=None) - datetime.timedelta(days=PURGE_DAYS)
    deleted = db.query(Notification).filter(
        Notification.recipient_id == current_user.id,
        Notification.is_read == True,  # noqa: E712
        Notification.created_at < cutoff,
    ).delete(synchronize_session=False)
    db.commit()
    return {"deleted": deleted}


@router.post("/{notification_id}/read", response_model=OkResponse, summary="Mark one notification as read")
def mark_read(
    notification_id: int,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    notif = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.recipient_id == current_user.id,
    ).first()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    notif.is_read = True
    db.commit()
    return {"ok": True}


@router.delete("/{notification_id}", status_code=204, summary="Dismiss one notification")
def dismiss(
    notification_id: int,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    notif = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.recipient_id == current_user.id,
    ).first()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    db.delete(notif)
    db.commit()
