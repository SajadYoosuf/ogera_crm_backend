"""Live notification stream (GET /notifications/stream): pushes only notifications created after connect."""
import asyncio
import json

from database import SessionLocal
from routes.notifications import _event_stream
from services.notification_service import create_notification


class _Connected:
    async def is_disconnected(self):
        return False


class _Disconnects:
    def __init__(self, after_checks):
        self.checks = 0
        self.after_checks = after_checks

    async def is_disconnected(self):
        self.checks += 1
        return self.checks > self.after_checks


async def _collect(gen):
    return [chunk async for chunk in gen]


def _events(chunks):
    out = []
    for chunk in chunks:
        if chunk.startswith("event: notification"):
            out.append(json.loads(chunk.split("data: ", 1)[1].strip()))
    return out


def test_stream_pushes_new_notification_once(client, login):
    _, anu = login("anu")
    schema = "test"

    async def run():
        gen = _event_stream(_Connected(), schema, anu["id"], interval=0.01, lifetime=0.6, heartbeat=0.05)
        assert (await gen.__anext__()).startswith("retry:")  # connection opened, nothing pending yet
        with SessionLocal() as db:
            create_notification(db, anu["id"], "lead_changed", "Lead updated by Admin", "Admin updated notes.", "lead", 7)
            db.commit()
        return await _collect(gen)

    events = _events(asyncio.run(run()))
    assert [e["title"] for e in events] == ["Lead updated by Admin"]
    assert events[0]["entity_id"] == 7


def test_stream_skips_notifications_that_existed_before_connect(client, login):
    _, anu = login("anu")
    with SessionLocal() as db:
        create_notification(db, anu["id"], "lead_changed", "Old one", "before connect", "lead", 1)
        db.commit()
    gen = _event_stream(_Connected(), "test", anu["id"], interval=0.01, lifetime=0.3, heartbeat=0.05)
    assert _events(asyncio.run(_collect(gen))) == []


def test_stream_ends_when_client_disconnects(client, login):
    _, anu = login("anu")
    request = _Disconnects(after_checks=2)
    gen = _event_stream(request, "test", anu["id"], interval=0.01, lifetime=30, heartbeat=0.05)
    chunks = asyncio.run(_collect(gen))
    assert chunks == ["retry: 3000\n\n"]
    assert request.checks == 3
