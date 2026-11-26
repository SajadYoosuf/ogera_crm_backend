"""GET /dashboard/summary: which view each role gets, whose data it holds, and how the numbers are counted.

Heads = admin, manager. Sales executives = anu, raj. "cara" (tele caller) and "sue" (support) are added here.
"""
import datetime

import pytest
from sqlalchemy import select

from conftest import SessionLocal
from seed_data import PASSWORD, email_of
from auth import get_password_hash
from business_time import ist_day_start_utc, today_ist
from models import Employee, EmployeeLookup, LeadSource, LeadStatus, Role
from models.contact import Contact
from models.lead import Lead
from models.lead_stage import LeadStage
from models.pipeline_stage import PipelineStage
from models.record_log import RecordLog
from models.whatsapp_message import WhatsAppMessage

_PHONES = iter(f"96{n:08d}" for n in range(10_000_000, 99_999_999))
UTC = datetime.timezone.utc
DAY = datetime.timedelta(days=1)


@pytest.fixture(autouse=True)
def _setup(app, monkeypatch):
    monkeypatch.setattr("services.whatsapp_service.send_lead_welcome", lambda *args, **kwargs: None)
    # Push runs on another thread, which would share (and roll back) the one in-memory SQLite connection.
    monkeypatch.setattr("services.notification_service.dispatch_notification_push", lambda *args, **kwargs: None)
    with SessionLocal() as db:
        for i, (slug, name) in enumerate([
            ("new", "New"), ("contacted", "Contacted"), ("pre_qualified", "Pre-Qualified"),
            ("lost_lead", "Lost Lead"), ("junk_lead", "Junk Lead"),
        ]):
            db.add(LeadStatus(slug=slug, name=name, sort_order=i, is_active=True))
        for key, name, slug, phone in (
            ("cara", "Cara Caller", "tele_caller", "9000000011"),
            ("sue", "Sue Support", "support", "9000000012"),
        ):
            role = Role(slug=slug, name=name.split()[1])
            db.add(role)
            db.flush()
            db.add(Employee(name=name, email=email_of(key), password_hash=get_password_hash(PASSWORD),
                            role_id=role.id, phone=phone))
            db.add(EmployeeLookup(email=email_of(key), tenant_id=1))
        db.commit()


def _source(slug="website") -> int:
    with SessionLocal() as db:
        return db.scalars(select(LeadSource.id).where(LeadSource.slug == slug)).one()


def _lead(client, headers, assigned_to, source="website", **extra):
    body = {"first_name": "Dash", "last_name": "Lead", "phone": next(_PHONES), "source_id": _source(source),
            "assigned_to": assigned_to, **extra}
    r = client.post("/leads?allow_duplicate=false", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _task(client, headers, record_id, assigned_to, due_at, kind="leads", title="Call back"):
    r = client.post(f"/{kind}/{record_id}/interactions/task", headers=headers, json={
        "activity_type": "phone_call", "due_at": due_at.isoformat(), "assigned_to": assigned_to, "title": title,
    })
    assert r.status_code == 201, r.text
    return r.json()


def _log(client, headers, lead_id, activity_type="phone_call", outcome=None):
    body = {"activity_type": activity_type, "notes": "spoke"}
    if outcome:
        body["outcome"] = outcome
    r = client.post(f"/leads/{lead_id}/interactions/log", headers=headers, json=body)
    assert r.status_code == 201, r.text


def _contact(assigned_to, kind="consumer", name="Cust", age_days=0) -> int:
    created = datetime.datetime.now(UTC).replace(tzinfo=None) - age_days * DAY
    with SessionLocal() as db:
        phone = next(_PHONES)
        row = Contact(name=name, phone=phone, phone_key=phone, contact_type=kind, assigned_to=assigned_to,
                      created_at=created)
        db.add(row)
        db.commit()
        return row.id


def _stage(contact_id, slug, by, kind="consumer", minutes_ago=0, loss_reason=None):
    with SessionLocal() as db:
        stage = db.scalars(select(PipelineStage).where(
            PipelineStage.slug == slug, PipelineStage.contact_type == kind)).one()
        db.add(LeadStage(
            contact_id=contact_id, stage_id=stage.id, changed_by=by, loss_reason=loss_reason,
            changed_at=datetime.datetime.now(UTC).replace(tzinfo=None) - datetime.timedelta(minutes=minutes_ago),
        ))
        db.commit()


def _summary(client, headers, **params):
    r = client.get("/dashboard/summary", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_requires_sign_in(client):
    assert client.get("/dashboard/summary").status_code == 401


@pytest.mark.parametrize("key, view", [
    ("admin", "head"), ("manager", "head"), ("anu", "sales"), ("cara", "telecaller"), ("sue", "staff"),
])
def test_each_role_gets_its_view(client, login, key, view):
    headers, employee = login(key)
    body = _summary(client, headers)
    assert body["view"] == view
    head = view == "head"
    assert (body["team"] is not None) is head
    assert (body["employees"] is not None) is head
    assert (body["unassigned_leads"] is not None) is head
    assert (body["qualified_with_telecaller"] is not None) is head
    assert (body["newly_assigned"] is not None) is not head
    assert (body["whatsapp_setup"] is not None) is (key == "admin")
    assert body["employee_id"] == (None if head else employee["id"])


def test_staff_see_only_their_own_work_and_cannot_widen_it(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    raj_h, raj = login("raj")
    mine = _lead(client, admin_h, anu["id"])
    theirs = _lead(client, admin_h, raj["id"])
    soon = datetime.datetime.now(UTC) + 3 * DAY
    _task(client, admin_h, mine["id"], anu["id"], soon)
    _task(client, admin_h, theirs["id"], raj["id"], soon)
    _log(client, raj_h, theirs["id"])
    _contact(raj["id"])

    body = _summary(client, anu_h, assigned_to=raj["id"])

    assert body["employee_id"] == anu["id"]
    assert body["leads"]["open"] == 1
    assert body["followups"]["upcoming"] == 1
    assert body["activity"]["total"] == 0
    assert body["contacts"]["total"] == 0
    assert {row["entity_id"] for row in body["recent"]} == {mine["id"]}


def test_head_sees_everyone_and_can_look_at_one_employee(client, login):
    admin_h, _ = login("admin")
    manager_h, _ = login("manager")
    _, anu = login("anu")
    raj_h, raj = login("raj")
    _lead(client, admin_h, anu["id"])
    theirs = _lead(client, admin_h, raj["id"])
    _log(client, raj_h, theirs["id"])

    everyone = _summary(client, manager_h)
    one = _summary(client, manager_h, assigned_to=raj["id"])

    assert everyone["leads"]["open"] == 2 and everyone["activity"]["total"] == 1
    assert one["leads"]["open"] == 1 and one["employee_id"] == raj["id"]
    assert [row["employee_id"] for row in one["team"]] == [raj["id"]]
    assert one["unassigned_leads"] is None
    names = [row["name"] for row in everyone["team"]]
    assert "Ada Admin" not in names and "Raj Sales" in names
    assert client.get("/dashboard/summary", headers=manager_h, params={"assigned_to": 9999}).status_code == 404


def test_follow_ups_cover_leads_and_contacts_oldest_first(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    lead = _lead(client, admin_h, anu["id"])
    contact_id = _contact(anu["id"], name="Meera Contact")
    now = datetime.datetime.now(UTC)
    end_of_today = (ist_day_start_utc(today_ist()) + DAY).replace(tzinfo=UTC) - datetime.timedelta(seconds=30)
    _task(client, admin_h, lead["id"], anu["id"], now - 2 * DAY, title="Late lead call")
    _task(client, admin_h, contact_id, anu["id"], end_of_today, kind="contacts", title="Contact visit")
    _task(client, admin_h, lead["id"], anu["id"], now + 3 * DAY, title="Later")
    done = _task(client, admin_h, lead["id"], anu["id"], now + 4 * DAY, title="Done one")
    assert client.patch(f"/interactions/{done['id']}", headers=anu_h,
                        json={"status": "completed", "outcome": "done"}).status_code == 200

    followups = _summary(client, anu_h)["followups"]

    assert (followups["overdue"], followups["today"], followups["upcoming"]) == (1, 1, 1)
    assert followups["completed_in_period"] == 1
    assert [(t["title"], t["entity_type"], t["overdue"]) for t in followups["queue"]] == [
        ("Late lead call", "lead", True), ("Contact visit", "contact", False),
    ]
    assert followups["queue"][1]["name"] == "Meera Contact"
    assert followups["queue"][0]["due_at"].endswith("+00:00")


def test_not_contacted_means_no_logged_activity(client, login):
    admin_h, _ = login("admin")
    cara_h, cara = login("cara")
    first = _lead(client, admin_h, cara["id"])
    second = _lead(client, admin_h, cara["id"])
    closed = _lead(client, admin_h, cara["id"])
    assert client.put(f"/leads/{closed['id']}", headers=cara_h, json={"status": "junk_lead"}).status_code == 200
    # A status change alone does not count as contact
    assert client.put(f"/leads/{first['id']}", headers=cara_h, json={"status": "contacted"}).status_code == 200
    _log(client, cara_h, second["id"])

    leads = _summary(client, cara_h)["leads"]

    assert leads["uncontacted"] == 1
    assert [item["id"] for item in leads["uncontacted_items"]] == [first["id"]]
    assert leads["open"] == 2


def test_calls_and_connect_rate(client, login):
    admin_h, _ = login("admin")
    cara_h, cara = login("cara")
    lead = _lead(client, admin_h, cara["id"])
    for outcome in ("done", "not_now", "no_answer", None):
        _log(client, cara_h, lead["id"], "phone_call", outcome)
    _log(client, cara_h, lead["id"], "whatsapp", "done")

    activity = _summary(client, cara_h)["activity"]

    assert activity["total"] == 5 and activity["calls"] == 4
    assert (activity["calls_answered"], activity["calls_with_outcome"], activity["connect_rate"]) == (2, 3, 66.7)
    assert activity["by_outcome"] == {"done": 2, "not_now": 1, "no_answer": 1, "none": 1}
    assert activity["by_type"][0] == {"type": "phone_call", "label": "Phone call", "count": 4}
    assert len(activity["by_day"]) == 30 and activity["by_day"][-1] == {
        "date": today_ist().isoformat(), "total": 5, "calls": 4}


def test_sources_conversion_and_period(client, login):
    admin_h, _ = login("admin")
    _, anu = login("anu")
    won = _lead(client, admin_h, anu["id"], "website")
    _lead(client, admin_h, anu["id"], "website")
    _lead(client, admin_h, anu["id"], "meta")
    old = _lead(client, admin_h, anu["id"], "meta")
    gone = _lead(client, admin_h, anu["id"], "meta")
    with SessionLocal() as db:
        now = datetime.datetime.now(UTC).replace(tzinfo=None)
        db.get(Lead, old["id"]).created_at = now - 40 * DAY
        db.get(Lead, gone["id"]).deleted_at = now
        db.commit()
    assert client.post(f"/leads/{won['id']}/convert", headers=admin_h).status_code == 200

    leads = _summary(client, admin_h)["leads"]

    assert (leads["new_in_period"], leads["converted_in_period"], leads["conversion_rate"]) == (3, 1, 33.3)
    assert [(s["name"], s["count"], s["converted"]) for s in leads["by_source"]] == [
        ("Website", 2, 1), ("Meta", 1, 0)]
    assert {s["slug"]: s["count"] for s in leads["by_status"]} == {"new": 3, "converted": 1}
    assert leads["by_status"][-1]["name"] == "Converted"
    assert sum(point["created"] for point in leads["trend"]) == 3
    assert leads["trend"][-1]["converted"] == 1
    week = _summary(client, admin_h, period="today")
    assert len(week["leads"]["trend"]) == 7
    year = _summary(client, admin_h, period="custom",
                    date_from=(today_ist() - 100 * DAY).isoformat(), date_to=today_ist().isoformat())
    assert year["leads"]["trend_days_per_point"] == 7 and year["leads"]["new_in_period"] == 4


def test_conversion_credit_stays_with_whoever_converted(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    raj_h, raj = login("raj")
    lead = _lead(client, admin_h, anu["id"])
    assert client.post(f"/leads/{lead['id']}/convert", headers=anu_h).status_code == 200
    with SessionLocal() as db:
        db.get(Lead, lead["id"]).assigned_to = raj["id"]
        db.commit()

    assert _summary(client, anu_h)["leads"]["converted_in_period"] == 1
    assert _summary(client, raj_h)["leads"]["converted_in_period"] == 0
    team = {row["employee_id"]: row["converted"] for row in _summary(client, admin_h)["team"]}
    assert team[anu["id"]] == 1 and team[raj["id"]] == 0


def test_head_sees_unassigned_and_qualified_leads_waiting_with_a_telecaller(client, login):
    admin_h, _ = login("admin")
    _, anu = login("anu")
    cara_h, cara = login("cara")
    waiting = _lead(client, admin_h, cara["id"])
    handed = _lead(client, admin_h, anu["id"])
    orphan = _lead(client, admin_h, anu["id"])
    with SessionLocal() as db:
        db.get(Lead, waiting["id"]).status = "pre_qualified"
        db.get(Lead, handed["id"]).status = "pre_qualified"
        db.get(Lead, orphan["id"]).assigned_to = None
        db.commit()

    body = _summary(client, admin_h)

    assert body["unassigned_leads"]["count"] == 1
    assert body["unassigned_leads"]["items"][0]["id"] == orphan["id"]
    assert body["qualified_with_telecaller"]["count"] == 1
    assert body["qualified_with_telecaller"]["items"][0]["id"] == waiting["id"]


def test_a_lead_given_to_me_shows_as_newly_assigned(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    _, raj = login("raj")
    older = _lead(client, admin_h, raj["id"])
    with SessionLocal() as db:
        db.get(Lead, older["id"]).created_at = datetime.datetime.now(UTC).replace(tzinfo=None) - 90 * DAY
        db.commit()
    assert client.put(f"/leads/{older['id']}", headers=admin_h, json={"assigned_to": anu["id"]}).status_code == 200

    items = _summary(client, anu_h)["newly_assigned"]

    assert [item["id"] for item in items] == [older["id"]]
    assert items[0]["assigned_at"]


def test_contact_pipelines_use_the_current_stage(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    moved = _contact(anu["id"], age_days=30)
    lost = _contact(anu["id"], age_days=30)
    _contact(anu["id"], "dealer")                       # no stage
    fresh = _contact(anu["id"], "partner")
    _contact(None)                                      # unassigned
    _stage(moved, "connected", anu["id"], minutes_ago=60)
    _stage(moved, "quotation", anu["id"])
    _stage(lost, "lost", anu["id"], loss_reason="price")
    _stage(fresh, "connected", anu["id"], "partner")

    mine = _summary(client, anu_h)["contacts"]
    all_of_them = _summary(client, admin_h)["contacts"]

    by_type = {p["type"]: p for p in mine["pipelines"]}
    consumer = {s["name"]: s["count"] for s in by_type["consumer"]["stages"]}
    assert (consumer["Quotation"], consumer["Connected"], consumer["Lost"]) == (1, 0, 1)
    assert (by_type["dealer"]["total"], by_type["dealer"]["no_stage"]) == (1, 1)
    assert by_type["consumer"]["unassigned"] is None
    # Quiet for a week (moved) and no stage (dealer). Lost is finished, the partner is new and staged.
    assert mine["needs_action"] == 2
    assert {item["type"] for item in mine["needs_action_items"]} == {"consumer", "dealer"}
    assert [(r["reason"], r["label"], r["count"]) for r in mine["loss_reasons"]] == [("price", "Price too high", 1)]
    assert {p["type"]: p["unassigned"] for p in all_of_them["pipelines"]}["consumer"] == 1


def test_whatsapp_counts_follow_lead_visibility(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    raj_h, raj = login("raj")
    mine = _lead(client, admin_h, anu["id"])
    theirs = _lead(client, admin_h, raj["id"])
    with SessionLocal() as db:
        for lead_id, direction, status, sender in (
            (mine["id"], "in", "received", None), (mine["id"], "in", "received", None),
            (mine["id"], "out", "sent", anu["id"]), (theirs["id"], "in", "received", None),
            (theirs["id"], "in", "read", None),
        ):
            db.add(WhatsAppMessage(lead_id=lead_id, direction=direction, status=status, sent_by=sender,
                                   content_type="text", body="hi"))
        db.commit()

    own = _summary(client, anu_h)["whatsapp"]
    head = _summary(client, admin_h)

    assert (own["unread_conversations"], own["sent_in_period"]) == (1, 1)
    assert own["unread_items"][0]["lead_id"] == mine["id"] and own["unread_items"][0]["unread"] == 2
    assert head["whatsapp"]["unread_conversations"] == 2
    assert head["whatsapp_setup"] == {"status": "not_configured", "templates": {}}


def test_recent_updates_name_the_record_and_the_person(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    lead = _lead(client, admin_h, anu["id"])
    assert client.put(f"/leads/{lead['id']}", headers=anu_h, json={"status": "contacted"}).status_code == 200

    latest = _summary(client, anu_h)["recent"][0]

    assert (latest["label"], latest["detail"]) == ("Status changed", "New → Contacted")
    assert (latest["name"], latest["actor_name"]) == ("Dash Lead", "Anu Sales")
    with SessionLocal() as db:
        assert db.scalars(select(RecordLog)).first() is not None


@pytest.mark.parametrize("params, message", [
    ({"period": "custom"}, "Choose both"),
    ({"period": "custom", "date_from": "2026-02-02", "date_to": "2026-02-01"}, "on or before"),
    ({"period": "custom", "date_from": "2024-01-01", "date_to": "2026-01-01"}, "one year"),
])
def test_bad_custom_periods_are_refused(client, login, params, message):
    headers, _ = login("admin")
    r = client.get("/dashboard/summary", headers=headers, params=params)
    assert r.status_code == 422 and message in r.json()["detail"]
