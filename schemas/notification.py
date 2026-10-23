"""
Pydantic schemas for the notification API.

NotificationOut     — one notification as returned by GET /notifications.
NotificationListOut — full response with list + unread_count for the bell badge.
"""
import datetime
from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: int
    type: str
    title: str
    message: str
    entity_type: str | None
    entity_id: int | None
    is_read: bool
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


class NotificationListOut(BaseModel):
    items: list[NotificationOut]
    unread_count: int
