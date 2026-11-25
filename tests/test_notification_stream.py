"""Live notification stream (GET /notifications/stream): pushes only notifications created after connect."""
import json

from database import SessionLocal
from routes.notifications import _event_stream
from services.notification_service import create_notification


def _events(chunks):
    out = []
    for chunk in chunks:
        if chunk.startswith("event: notification"):
            out.append(json.loads(chunk.split("data: ", 1)[1].strip()))
    return out


def test_stream_pushes_new_notification_once(client, login):
    _, anu = login("anu")
    schema = "test"
    gen = _event_stream(schema, anu["id"], interval=0.01, lifetime=0.6, heartbeat=0.05)
    assert next(gen).startswith("retry:")  # connection opened, nothing pending yet

    with SessionLocal() as db:
        create_notification(db, anu["id"], "lead_changed", "Lead updated by Admin", "Admin updated notes.", "lead", 7)
        db.commit()

    events = _events(gen)
    assert [e["title"] for e in events] == ["Lead updated by Admin"]
    assert events[0]["entity_id"] == 7


def test_stream_skips_notifications_that_existed_before_connect(client, login):
    _, anu = login("anu")
    with SessionLocal() as db:
        create_notification(db, anu["id"], "lead_changed", "Old one", "before connect", "lead", 1)
        db.commit()
    gen = _event_stream("test", anu["id"], interval=0.01, lifetime=0.3, heartbeat=0.05)
    assert _events(gen) == []
