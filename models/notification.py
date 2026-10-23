"""
Notification model — stores every in-app and push notification sent to an employee.

WHY THIS TABLE?
    Every notification the system sends is persisted here so that:
    1. The web dashboard shows an in-app inbox (bell icon + unread count).
    2. Notifications sent while the user was offline are not lost.
    3. We can deduplicate — the 48-hour rule must not fire every hour for the same contact.

COLUMNS:
    recipient_id  — which employee should see this notification
    type          — short string like "contact_created" or "inactivity"
    title         — headline shown in the notification panel
    message       — full sentence shown below the title
    entity_type   — "contact", "task", or "followup" (what the notification is about)
    entity_id     — the id of that entity so the UI can link to it
    is_read       — False until the user dismisses it
    created_at    — when it was generated
"""
import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Which employee receives this — cascades on employee delete so orphan rows never pile up.
    recipient_id: Mapped[int] = mapped_column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(String(20))   # "contact" / "task" / "followup"
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
