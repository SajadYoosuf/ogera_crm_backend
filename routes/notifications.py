"""
Notification routes — the in-app notification inbox API.

ENDPOINTS (the first three keep the original contract the web/mobile apps use):
    GET    /notifications               — list my notifications, unread first; returns unread_count
    GET    /notifications/stream        — live push (server-sent events): one "notification" event per new row
    POST   /notifications/read-all      — mark all as read
    POST   /notifications/{id}/read     — mark one as read
    DELETE /notifications/{id}          — dismiss one notification
    POST   /notifications/purge         — delete notifications older than 30 days (read ones)

SECURITY:
    Every endpoint requires a valid JWT. Users only ever touch their OWN rows —
    recipient_id is always compared to the current user's id. The session is the
    tenant-scoped one (get_tenant_db), so rows live in the tenant schema.
"""
import asyncio
import datetime
import json
import time
from datetime import timezone as _tz

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import get_current_user, get_tenant_db
from database import get_session_for_schema
from models import Employee
from models.notification import Notification
from schemas.notification import NotificationListOut, NotificationOut

PURGE_DAYS = 30

# Live stream: the server checks the database for this user's new rows and pushes them.
# A connection closes after STREAM_LIFETIME_S; the client reconnects on its own.
STREAM_INTERVAL_S = 3.0
STREAM_LIFETIME_S = 600.0
STREAM_HEARTBEAT_S = 15.0
# Rows committed slightly late still get pushed: each check looks back this far and skips ids already sent.
STREAM_LOOKBACK = datetime.timedelta(minutes=2)


class OkResponse(BaseModel):
    ok: bool = True


class PurgeResponse(BaseModel):
    deleted: int


router = APIRouter(
    prefix="/notifications",
    tags=["notifications"],
    dependencies=[Depends(get_current_user)],
)


def _utc_naive_now() -> datetime.datetime:
    return datetime.datetime.now(_tz.utc).replace(tzinfo=None)


def _recent_for(schema: str, recipient_id: int, since: datetime.datetime) -> list[dict]:
    """This user's notifications created at or after `since`, oldest first, as JSON-ready dicts."""
    with get_session_for_schema(schema) as db:
        rows = (
            db.query(Notification)
            .filter(Notification.recipient_id == recipient_id, Notification.created_at >= since)
            .order_by(Notification.id)
            .all()
        )
        return [NotificationOut.model_validate(r).model_dump(mode="json") for r in rows]


async def _event_stream(request: Request, schema: str, recipient_id: int, interval: float = STREAM_INTERVAL_S,
                        lifetime: float = STREAM_LIFETIME_S, heartbeat: float = STREAM_HEARTBEAT_S):
    """
    Yields server-sent events. Only notifications created after the connection opens are pushed:
    the ids already present when it starts are remembered and skipped. Stops as soon as the
    client disconnects, so a closed tab does not keep a server task and DB polling alive.
    """
    started = time.monotonic()
    last_sent = started
    seen = {n["id"] for n in await asyncio.to_thread(_recent_for, schema, recipient_id, _utc_naive_now() - STREAM_LOOKBACK)}
    yield "retry: 3000\n\n"
    while time.monotonic() - started < lifetime:
        await asyncio.sleep(interval)
        if await request.is_disconnected():
            return
        items = await asyncio.to_thread(_recent_for, schema, recipient_id, _utc_naive_now() - STREAM_LOOKBACK)
        for item in items:
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            yield f"event: notification\ndata: {json.dumps(item)}\n\n"
            last_sent = time.monotonic()
        if time.monotonic() - last_sent >= heartbeat:
            yield ": ping\n\n"  # comment line keeps proxies from closing an idle connection
            last_sent = time.monotonic()


@router.get("/stream", summary="Live notifications (server-sent events)")
def stream_notifications(request: Request, current_user: Employee = Depends(get_current_user)):
    """
    Keeps the connection open and sends `event: notification` with the notification JSON as soon as
    it is created for the caller. The stream ends after ~10 minutes or when the client disconnects;
    the client reconnects. The token goes in the Authorization header, same as every other endpoint.
    """
    return StreamingResponse(
        _event_stream(request, current_user._tenant_schema, current_user.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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
