"""
Notification routes — the in-app notification inbox API.

WHAT THIS FILE DOES:
    Exposes three endpoints that the web dashboard bell icon calls:
      GET  /notifications        — list the current user's notifications (newest unread first)
      POST /notifications/{id}/read — mark one as read
      POST /notifications/read-all  — mark all as read

SECURITY:
    Every endpoint requires a valid JWT. Users can only read and update their OWN
    notifications — recipient_id is always compared to the current user's id.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from auth import get_current_user
from database import get_db
from models import Employee
from models.notification import Notification
from schemas.notification import NotificationListOut

router = APIRouter(
    prefix="/notifications",
    tags=["notifications"],
    dependencies=[Depends(get_current_user)],
)


@router.get("", response_model=NotificationListOut, summary="List my notifications")
def list_notifications(
    skip: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /notifications — Returns the current user's notification inbox.

    Unread notifications appear first (sorted by newest). Older read ones follow.
    Also returns unread_count so the bell badge stays accurate between polls.
    """
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


@router.post("/read-all", summary="Mark all notifications as read")
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """POST /notifications/read-all — clears the unread badge for this user."""
    db.query(Notification).filter(
        Notification.recipient_id == current_user.id,
        Notification.is_read == False,  # noqa: E712
    ).update({"is_read": True})
    db.commit()
    return {"ok": True}


@router.post("/{notification_id}/read", summary="Mark one notification as read")
def mark_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """POST /notifications/{id}/read — marks one notification read."""
    notif = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.recipient_id == current_user.id,
    ).first()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    notif.is_read = True
    db.commit()
    return {"ok": True}
