"""WhatsApp inbox: role scope, unread tracking, assignment, and the inbound webhook.

Heads (admin, manager) see every conversation; staff (sales executives) only leads assigned to them.
The shared test seed leaves roles.see_all_records False, so the fixture below sets it the way the
production tenant seed does.
"""
import pytest
from sqlalchemy import select, update

from conftest import SessionLocal
from models import LeadStatus, Notification, Role
from models.lead import Lead
from models.whatsapp_message import WhatsAppMessage

_PHONES = iter(f"98{n:08d}" for n in range(30_000_000, 99_999_999))


@pytest.fixture(autouse=True)
def _heads_and_statuses(app):
    with SessionLocal() as db:
        db.execute(update(Role).where(Role.slug.in_(["admin", "manager"])).values(see_all_records=True))
        for i, (slug, name) in enumerate([("new", "New"), ("contacted", "Contacted")]):
            db.add(LeadStatus(slug=slug, name=name, sort_order=i, is_active=True))
        db.commit()


def _lead(client, headers, assigned_to=None):
    body = {"first_name": "Asha", "phone": next(_PHONES)}
    if assigned_to is not None:
        body["assigned_to"] = assigned_to
    r = client.post("/leads?allow_duplicate=false", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _message(lead_id, direction="in", body="hello", is_read=None, wamid=None):
    with SessionLocal() as db:
        db.add(WhatsAppMessage(
            lead_id=lead_id, direction=direction, body=body, content_type="text", status="sent",
            wamid=wamid, is_read=(direction == "out") if is_read is None else is_read,
        ))
        db.commit()


def _ids(client, headers):
    return [c["lead_id"] for c in client.get("/whatsapp-inbox/conversations", headers=headers).json()["items"]]


def test_head_sees_all_conversations_and_staff_only_their_own(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    _, raj = login("raj")
    mine = _lead(client, admin_h, anu["id"])
    other = _lead(client, admin_h, raj["id"])
    _message(mine["id"]); _message(other["id"])

    assert set(_ids(client, admin_h)) == {mine["id"], other["id"]}
    assert _ids(client, anu_h) == [mine["id"]]


def test_leads_without_messages_are_not_conversations(client, login):
    admin_h, _ = login("admin")
    _lead(client, admin_h)
    assert _ids(client, admin_h) == []


def test_conversation_shows_last_message_unread_count_and_assignee(client, login):
    admin_h, _ = login("admin")
    _, anu = login("anu")
    lead = _lead(client, admin_h, anu["id"])
    _message(lead["id"], "in", "first")
    _message(lead["id"], "in", "price?")
    _message(lead["id"], "out", "sending details")

    row = client.get("/whatsapp-inbox/conversations", headers=admin_h).json()["items"][0]
    assert row["unread_count"] == 2
    assert row["last_message"]["body"] == "sending details"
    assert row["last_message"]["direction"] == "out"
    assert row["assigned_name"] == "Anu Sales"


def test_newest_conversation_comes_first(client, login):
    admin_h, _ = login("admin")
    a = _lead(client, admin_h); b = _lead(client, admin_h)
    _message(a["id"]); _message(b["id"])
    assert _ids(client, admin_h) == [b["id"], a["id"]]


def test_search_matches_phone_and_name(client, login):
    admin_h, _ = login("admin")
    lead = _lead(client, admin_h)
    _message(lead["id"])
    assert _ids(client, admin_h) == [lead["id"]]
    found = client.get("/whatsapp-inbox/conversations", params={"search": lead["phone"][-6:]}, headers=admin_h).json()
    assert [c["lead_id"] for c in found["items"]] == [lead["id"]]
    none = client.get("/whatsapp-inbox/conversations", params={"search": "zzzz"}, headers=admin_h).json()
    assert none["items"] == [] and none["total"] == 0


def test_unread_count_counts_conversations_visible_to_the_user(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    _, raj = login("raj")
    mine = _lead(client, admin_h, anu["id"]); other = _lead(client, admin_h, raj["id"])
    _message(mine["id"]); _message(mine["id"]); _message(other["id"])

    assert client.get("/whatsapp-inbox/unread-count", headers=admin_h).json() == {"count": 2}
    assert client.get("/whatsapp-inbox/unread-count", headers=anu_h).json() == {"count": 1}


def test_mark_read_clears_unread(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    lead = _lead(client, admin_h, anu["id"])
    _message(lead["id"])

    assert client.post(f"/whatsapp-inbox/conversations/{lead['id']}/mark-read", headers=anu_h).json() == {"ok": True}
    assert client.get("/whatsapp-inbox/unread-count", headers=anu_h).json() == {"count": 0}
    row = client.get("/whatsapp-inbox/conversations", headers=anu_h).json()["items"][0]
    assert row["unread_count"] == 0


def test_staff_cannot_mark_someone_elses_conversation_read(client, login):
    admin_h, _ = login("admin")
    anu_h, _ = login("anu")
    _, raj = login("raj")
    lead = _lead(client, admin_h, raj["id"])
    _message(lead["id"])

    assert client.post(f"/whatsapp-inbox/conversations/{lead['id']}/mark-read", headers=anu_h).status_code == 403


def test_head_assigns_conversation_and_staff_then_sees_it(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    lead = _lead(client, admin_h)
    _message(lead["id"])
    assert _ids(client, anu_h) == []

    r = client.post(f"/whatsapp-inbox/conversations/{lead['id']}/assign", json={"employee_id": anu["id"]}, headers=admin_h)

    assert r.status_code == 200, r.text
    assert _ids(client, anu_h) == [lead["id"]]


def test_staff_cannot_assign_a_conversation(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    lead = _lead(client, admin_h, anu["id"])
    _message(lead["id"])

    r = client.post(f"/whatsapp-inbox/conversations/{lead['id']}/assign", json={"employee_id": anu["id"]}, headers=anu_h)
    assert r.status_code == 403


def test_assign_rejects_an_unknown_employee(client, login):
    admin_h, _ = login("admin")
    lead = _lead(client, admin_h)
    _message(lead["id"])
    r = client.post(f"/whatsapp-inbox/conversations/{lead['id']}/assign", json={"employee_id": 99999}, headers=admin_h)
    assert r.status_code == 400


def test_inbox_requires_sign_in(client):
    assert client.get("/whatsapp-inbox/conversations").status_code == 401
    assert client.get("/whatsapp-inbox/unread-count").status_code == 401


# ── Inbound webhook ──────────────────────────────────────────────────────────

def _inbound(phone, wamid, text="Hi, need a quote"):
    return {"source": "whatsapp_inbound", "message_id": wamid, "message_type": "text", "message_text": text, "timestamp": "1"}


def _notified(type_):
    with SessionLocal() as db:
        return sorted(n.recipient_id for n in db.scalars(select(Notification).where(Notification.type == type_)).all())


def test_webhook_stores_inbound_message_for_a_new_lead_and_notifies_heads(client, login):
    from routes.webhooks import _process_whatsapp_lead
    _, admin = login("admin"); _, manager = login("manager"); _, anu = login("anu")
    phone = "919876500001"

    _process_whatsapp_lead(phone, _inbound(phone, "wamid.A1"), "test")

    with SessionLocal() as db:
        msg = db.scalars(select(WhatsAppMessage)).one()
        assert (msg.direction, msg.body, msg.is_read, msg.wamid) == ("in", "Hi, need a quote", False, "wamid.A1")
        assert db.get(Lead, msg.lead_id) is not None
    assert _notified("whatsapp_inbound") == sorted([admin["id"], manager["id"]])
    assert anu["id"] not in _notified("whatsapp_inbound")


def test_webhook_ignores_a_redelivered_message(client, login):
    from routes.webhooks import _process_whatsapp_lead
    login("admin")
    phone = "919876500002"

    _process_whatsapp_lead(phone, _inbound(phone, "wamid.B1"), "test")
    _process_whatsapp_lead(phone, _inbound(phone, "wamid.B1"), "test")

    with SessionLocal() as db:
        assert len(db.scalars(select(WhatsAppMessage)).all()) == 1


def test_webhook_on_existing_lead_notifies_heads_and_the_assignee(client, login):
    from routes.webhooks import _process_whatsapp_lead
    admin_h, admin = login("admin"); _, manager = login("manager"); _, anu = login("anu")
    lead = _lead(client, admin_h, anu["id"])
    phone = lead["phone"]

    _process_whatsapp_lead(phone, _inbound(phone, "wamid.C1", "Is it in stock?"), "test")

    with SessionLocal() as db:
        msg = db.scalars(select(WhatsAppMessage).where(WhatsAppMessage.lead_id == lead["id"])).one()
        assert msg.body == "Is it in stock?" and msg.is_read is False
        assert db.get(Lead, lead["id"]).status == "contacted"
    assert _notified("whatsapp_inbound") == sorted([admin["id"], manager["id"], anu["id"]])
