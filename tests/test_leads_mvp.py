"""Leads MVP: role scope, interactions, notifications, lead statuses.

Runs against the in-memory SQLite app from conftest.py. Staff = sales executives
(anu, raj, sam); heads = admin and manager.
"""
import datetime

import pytest

from conftest import SessionLocal
from models import LeadStatus, Notification
from seed_data import MOBILE_APP

FUTURE = (datetime.datetime.now() + datetime.timedelta(days=2)).replace(microsecond=0).isoformat()
_PHONES = iter(f"98{n:08d}" for n in range(20_000_000, 99_999_999))


@pytest.fixture(autouse=True)
def _statuses(app):
    with SessionLocal() as db:
        for i, (slug, name) in enumerate([("new", "New"), ("contacted", "Contacted"), ("qualified", "Qualified"), ("lost", "Lost")]):
            db.add(LeadStatus(slug=slug, name=name, sort_order=i, is_active=True))
        db.commit()


def _create_lead(client, headers, assigned_to=None, **extra):
    body = {"first_name": "Test", "phone": next(_PHONES), **extra}
    if assigned_to is not None:
        body["assigned_to"] = assigned_to
    r = client.post("/leads?allow_duplicate=false", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _assign(client, admin_headers, lead_id, employee_id):
    r = client.put(f"/leads/{lead_id}", json={"assigned_to": employee_id}, headers=admin_headers)
    assert r.status_code == 200, r.text


# ── Phase 1: role scope ──────────────────────────────────────────────────────

def test_staff_list_shows_only_own_leads(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    _, raj = login("raj")
    mine = _create_lead(client, admin_h, assigned_to=anu["id"])
    other = _create_lead(client, admin_h, assigned_to=raj["id"])

    ids = {l["id"] for l in client.get("/leads", headers=anu_h).json()["items"]}
    assert mine["id"] in ids and other["id"] not in ids


def test_head_list_shows_all_leads(client, login):
    admin_h, _ = login("admin")
    _, anu = login("anu")
    _, raj = login("raj")
    a = _create_lead(client, admin_h, assigned_to=anu["id"])
    b = _create_lead(client, admin_h, assigned_to=raj["id"])
    ids = {l["id"] for l in client.get("/leads", headers=admin_h).json()["items"]}
    assert {a["id"], b["id"]} <= ids


def test_staff_cannot_open_or_edit_someone_elses_lead(client, login):
    admin_h, _ = login("admin")
    anu_h, _ = login("anu")
    _, raj = login("raj")
    lead = _create_lead(client, admin_h, assigned_to=raj["id"])
    assert client.get(f"/leads/{lead['id']}", headers=anu_h).status_code == 403
    assert client.put(f"/leads/{lead['id']}", json={"notes": "x"}, headers=anu_h).status_code == 403
    assert client.post(f"/leads/{lead['id']}/convert", headers=anu_h).status_code == 403


def test_staff_can_open_and_edit_own_lead(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    lead = _create_lead(client, admin_h, assigned_to=anu["id"])
    assert client.get(f"/leads/{lead['id']}", headers=anu_h).status_code == 200
    assert client.put(f"/leads/{lead['id']}", json={"notes": "called"}, headers=anu_h).status_code == 200


def test_staff_cannot_reassign_or_delete(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    _, raj = login("raj")
    lead = _create_lead(client, admin_h, assigned_to=anu["id"])
    assert client.put(f"/leads/{lead['id']}", json={"assigned_to": raj["id"]}, headers=anu_h).status_code == 403
    assert client.delete(f"/leads/{lead['id']}", headers=anu_h).status_code == 403
    assert client.delete(f"/leads/{lead['id']}", headers=admin_h).status_code == 204


def test_staff_cannot_choose_assignee_on_create(client, login):
    anu_h, _ = login("anu")
    _, raj = login("raj")
    r = client.post("/leads", json={"first_name": "X", "phone": next(_PHONES), "assigned_to": raj["id"]}, headers=anu_h)
    assert r.status_code == 403


def test_head_can_choose_assignee_on_create(client, login):
    admin_h, _ = login("admin")
    _, raj = login("raj")
    assert _create_lead(client, admin_h, assigned_to=raj["id"])["assigned_to"] == raj["id"]


# ── Phase 2: interactions ────────────────────────────────────────────────────

def test_staff_logs_activity_on_own_lead_and_lists_it(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    lead = _create_lead(client, admin_h, assigned_to=anu["id"])
    r = client.post(f"/leads/{lead['id']}/interactions/log", headers=anu_h,
                    json={"activity_type": "phone_call", "notes": "spoke", "outcome": "done"})
    assert r.status_code == 201, r.text
    rows = client.get(f"/leads/{lead['id']}/interactions", headers=anu_h).json()
    assert len(rows) == 1 and rows[0]["kind"] == "log" and rows[0]["done_by_name"]
    assert rows[0]["logged_at"] and rows[0]["overdue_days"] == 0


def test_log_validation(client, login):
    admin_h, _ = login("admin")
    lead = _create_lead(client, admin_h)
    url = f"/leads/{lead['id']}/interactions/log"
    assert client.post(url, headers=admin_h, json={"activity_type": "note"}).status_code == 422          # notes required
    assert client.post(url, headers=admin_h, json={"activity_type": "note", "notes": "  "}).status_code == 422
    assert client.post(url, headers=admin_h, json={"activity_type": "other", "notes": "x"}).status_code == 422  # label required
    assert client.post(url, headers=admin_h, json={"activity_type": "note", "notes": "x", "outcome": "maybe"}).status_code == 422
    ok = client.post(url, headers=admin_h, json={"activity_type": "other", "custom_label": "Demo visit", "notes": "x"})
    assert ok.status_code == 201 and ok.json()["custom_label"] == "Demo visit"


def test_staff_cannot_log_on_others_lead(client, login):
    admin_h, _ = login("admin")
    anu_h, _ = login("anu")
    _, raj = login("raj")
    lead = _create_lead(client, admin_h, assigned_to=raj["id"])
    r = client.post(f"/leads/{lead['id']}/interactions/log", headers=anu_h, json={"activity_type": "note", "notes": "x"})
    assert r.status_code == 403


def test_only_heads_can_log_for_someone_else_or_backdate(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    _, raj = login("raj")
    lead = _create_lead(client, admin_h, assigned_to=anu["id"])
    url = f"/leads/{lead['id']}/interactions/log"
    base = {"activity_type": "note", "notes": "x"}
    assert client.post(url, headers=anu_h, json={**base, "done_by": raj["id"]}).status_code == 403
    assert client.post(url, headers=anu_h, json={**base, "logged_at": "2026-01-01T10:00:00"}).status_code == 403
    r = client.post(url, headers=admin_h, json={**base, "done_by": raj["id"]})
    assert r.status_code == 201 and r.json()["done_by"] == raj["id"]


def _task(client, headers, lead_id, assigned_to, **extra):
    return client.post(f"/leads/{lead_id}/interactions/task", headers=headers,
                       json={"activity_type": "phone_call", "due_at": FUTURE, "assigned_to": assigned_to, **extra})


def test_staff_can_only_schedule_tasks_for_themselves(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    _, raj = login("raj")
    lead = _create_lead(client, admin_h, assigned_to=anu["id"])
    ok = _task(client, anu_h, lead["id"], anu["id"], notes="call back")
    assert ok.status_code == 201
    body = ok.json()
    assert body["assigned_to"] == anu["id"] and body["status"] == "todo" and body["priority"] == "medium"
    assert body["title"]  # derived from the activity type when omitted
    assert _task(client, anu_h, lead["id"], raj["id"]).status_code == 403


def test_task_with_unknown_assignee_is_rejected(client, login):
    admin_h, _ = login("admin")
    lead = _create_lead(client, admin_h)
    assert _task(client, admin_h, lead["id"], 99999).status_code == 400


def test_head_assigns_task_and_assignee_is_notified(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    lead = _create_lead(client, admin_h, assigned_to=anu["id"])
    r = _task(client, admin_h, lead["id"], anu["id"], title="Send quote")
    assert r.status_code == 201, r.text
    notes = client.get("/notifications", headers=anu_h).json()
    assert any(n["type"] == "followup_assigned" for n in notes["items"])


def test_complete_and_reschedule_rules(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    raj_h, raj = login("raj")
    lead = _create_lead(client, admin_h, assigned_to=anu["id"])
    task = _task(client, anu_h, lead["id"], anu["id"]).json()
    resched = f"/leads/{lead['id']}/interactions/{task['id']}/reschedule"
    update = f"/interactions/{task['id']}"
    done = {"status": "completed", "outcome": "done"}
    assert client.patch(update, headers=raj_h, json=done).status_code == 403
    assert client.patch(resched, headers=raj_h, json={"due_at": FUTURE}).status_code == 403
    assert client.patch(resched, headers=anu_h, json={"due_at": FUTURE}).status_code == 200
    assert client.patch(update, headers=anu_h, json={"status": "completed"}).status_code == 422  # outcome required
    assert client.patch(update, headers=anu_h, json={"status": "inprogress"}).json()["status"] == "inprogress"
    finished = client.patch(update, headers=anu_h, json={**done, "notes": "all good"})
    assert finished.status_code == 200 and finished.json()["status"] == "completed"
    assert finished.json()["outcome"] == "done" and "all good" in finished.json()["notes"]


def test_head_can_complete_any_task(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    lead = _create_lead(client, admin_h, assigned_to=anu["id"])
    task = _task(client, anu_h, lead["id"], anu["id"]).json()
    r = client.patch(f"/interactions/{task['id']}", headers=admin_h, json={"status": "completed", "outcome": "no_answer"})
    assert r.status_code == 200


def test_overdue_days_counts_only_open_tasks(client, login):
    admin_h, _ = login("admin")
    lead = _create_lead(client, admin_h)
    past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=3, hours=1)).isoformat()
    task = _task(client, admin_h, lead["id"], login("admin")[1]["id"], due_at=past).json()
    assert task["overdue_days"] >= 3
    done = client.patch(f"/interactions/{task['id']}", headers=admin_h, json={"status": "completed", "outcome": "done"}).json()
    assert done["overdue_days"] == 0


def test_cannot_complete_a_log_entry(client, login):
    admin_h, _ = login("admin")
    lead = _create_lead(client, admin_h)
    log = client.post(f"/leads/{lead['id']}/interactions/log", headers=admin_h,
                      json={"activity_type": "note", "notes": "x"}).json()
    r = client.patch(f"/interactions/{log['id']}", headers=admin_h, json={"status": "completed", "outcome": "done"})
    assert r.status_code == 400


def test_reassign_and_delete_are_head_only(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    _, raj = login("raj")
    lead = _create_lead(client, admin_h, assigned_to=anu["id"])
    task = _task(client, anu_h, lead["id"], anu["id"]).json()
    base = f"/leads/{lead['id']}/interactions/{task['id']}"
    assert client.patch(f"{base}/reassign", headers=anu_h, json={"assigned_to": raj["id"]}).status_code == 403
    assert client.delete(base, headers=anu_h).status_code == 403
    assert client.patch(f"{base}/reassign", headers=admin_h, json={"assigned_to": raj["id"]}).json()["assigned_to"] == raj["id"]
    assert client.delete(base, headers=admin_h).status_code == 204
    assert client.get(f"/leads/{lead['id']}/interactions", headers=admin_h).json() == []


def test_interaction_on_wrong_lead_is_404(client, login):
    admin_h, _ = login("admin")
    a = _create_lead(client, admin_h)
    b = _create_lead(client, admin_h)
    task = _task(client, admin_h, a["id"], login("admin")[1]["id"]).json()
    assert client.patch(f"/leads/{b['id']}/interactions/{task['id']}/reschedule", headers=admin_h,
                        json={"due_at": FUTURE}).status_code == 404


# ── Phase 3: notifications ───────────────────────────────────────────────────

def test_assignment_notifies_assignee_and_heads_see_new_lead(client, login):
    admin_h, _ = login("admin")
    manager_h, _ = login("manager")
    anu_h, anu = login("anu")
    _create_lead(client, admin_h, assigned_to=anu["id"])
    anu_notes = client.get("/notifications", headers=anu_h).json()
    assert anu_notes["unread_count"] >= 1 and anu_notes["items"][0]["type"] == "lead_assigned"
    manager_notes = client.get("/notifications", headers=manager_h).json()
    assert any(n["type"] == "lead_created" for n in manager_notes["items"])


def test_reassigning_a_lead_notifies_new_assignee(client, login):
    admin_h, _ = login("admin")
    raj_h, raj = login("raj")
    lead = _create_lead(client, admin_h)
    _assign(client, admin_h, lead["id"], raj["id"])
    assert any(n["type"] == "lead_assigned" for n in client.get("/notifications", headers=raj_h).json()["items"])


def test_mark_read_read_all_dismiss_are_per_user(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    raj_h, _ = login("raj")
    _create_lead(client, admin_h, assigned_to=anu["id"])
    _create_lead(client, admin_h, assigned_to=anu["id"])
    items = client.get("/notifications", headers=anu_h).json()["items"]
    first = items[0]["id"]

    assert client.post(f"/notifications/{first}/read", headers=raj_h).status_code == 404
    assert client.delete(f"/notifications/{first}", headers=raj_h).status_code == 404

    assert client.post(f"/notifications/{first}/read", headers=anu_h).status_code == 200
    assert client.get("/notifications", headers=anu_h).json()["unread_count"] == len(items) - 1
    assert client.post("/notifications/read-all", headers=anu_h).status_code == 200
    assert client.get("/notifications", headers=anu_h).json()["unread_count"] == 0
    assert client.delete(f"/notifications/{first}", headers=anu_h).status_code == 204
    assert len(client.get("/notifications", headers=anu_h).json()["items"]) == len(items) - 1


def test_purge_removes_only_old_read_notifications(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    _create_lead(client, admin_h, assigned_to=anu["id"])
    old = datetime.datetime.utcnow() - datetime.timedelta(days=45)
    with SessionLocal() as db:
        db.add(Notification(recipient_id=anu["id"], type="x", title="old read", message="m", is_read=True, created_at=old))
        db.add(Notification(recipient_id=anu["id"], type="x", title="old unread", message="m", is_read=False, created_at=old))
        db.commit()
    assert client.post("/notifications/purge", headers=anu_h).json() == {"deleted": 1}
    titles = {n["title"] for n in client.get("/notifications", headers=anu_h).json()["items"]}
    assert "old read" not in titles and "old unread" in titles


def _types_for(client, headers):
    return [n["type"] for n in client.get("/notifications", headers=headers).json()["items"]]


def test_due_today_reminder_once_per_day_and_skips_completed(client, login):
    from business_time import ist_day_start_utc, today_ist
    from services.followup_reminders import send_due_today

    admin_h, admin = login("admin")
    anu_h, anu = login("anu")
    lead = _create_lead(client, admin_h, assigned_to=anu["id"])
    # 10:00 IST today, and a second task on the same lead (one reminder per lead)
    due = (ist_day_start_utc(today_ist()) + datetime.timedelta(hours=10)).replace(tzinfo=datetime.timezone.utc).isoformat()
    _task(client, anu_h, lead["id"], anu["id"], due_at=due)
    _task(client, anu_h, lead["id"], anu["id"], due_at=due)
    done = _task(client, admin_h, lead["id"], admin["id"], due_at=due).json()
    client.patch(f"/interactions/{done['id']}", headers=admin_h, json={"status": "completed", "outcome": "done"})

    with SessionLocal() as db:
        assert send_due_today(db) == 1
        assert send_due_today(db) == 0  # dedup on a second run the same day
    assert _types_for(client, anu_h).count("followup_due") == 1
    assert "followup_due" not in _types_for(client, admin_h)


def test_overdue_reminder_after_24h_once(client, login):
    from services.followup_reminders import send_overdue

    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    lead = _create_lead(client, admin_h, assigned_to=anu["id"])
    now = datetime.datetime.now(datetime.timezone.utc)
    _task(client, anu_h, lead["id"], anu["id"], due_at=(now - datetime.timedelta(hours=23)).isoformat())
    with SessionLocal() as db:
        assert send_overdue(db) == 0  # not yet 24 h late
    _task(client, anu_h, lead["id"], anu["id"], due_at=(now - datetime.timedelta(hours=30)).isoformat())
    with SessionLocal() as db:
        assert send_overdue(db) == 1
        assert send_overdue(db) == 0
    assert _types_for(client, anu_h).count("followup_overdue") == 1


@pytest.fixture(autouse=True)
def _heads_see_all(client):
    """The test seed creates roles with see_all_records=False; production sets it for admin/manager. Mirror that here."""
    from sqlalchemy import select
    from models.role import Role
    with SessionLocal() as db:
        for role in db.scalars(select(Role).where(Role.slug.in_(("admin", "manager")))).all():
            role.see_all_records = True
        db.commit()
    yield


def test_staff_logged_activity_notifies_admin_not_the_actor(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    lead = _create_lead(client, anu_h)  # staff lead: assigned to anu
    r = client.post(f"/leads/{lead['id']}/interactions/log", headers=anu_h,
                    json={"activity_type": "phone_call", "notes": "called, will call back"})
    assert r.status_code == 201, r.text
    assert _types_for(client, admin_h).count("activity_logged") == 1
    assert "activity_logged" not in _types_for(client, anu_h)


def test_admin_logged_activity_does_not_notify_heads(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    lead = _create_lead(client, admin_h, assigned_to=anu["id"])
    client.post(f"/leads/{lead['id']}/interactions/log", headers=admin_h,
                json={"activity_type": "visit", "notes": "site visit"})
    assert "activity_logged" not in _types_for(client, admin_h)


def test_reminders_run_per_tenant_and_survive_errors(monkeypatch):
    import services.followup_reminders as fr

    monkeypatch.setattr(fr, "_active_schemas", lambda: ["test", "Bad Schema!"])
    calls = []

    def job(db):
        calls.append(db)
        return 0

    fr.run_for_all_tenants(job)  # invalid schema name is logged, not raised
    assert len(calls) == 1


# ── Phase 4: lead statuses ───────────────────────────────────────────────────

def test_statuses_list_for_anyone_write_for_admin_only(client, login):
    admin_h, _ = login("admin")
    manager_h, _ = login("manager")
    anu_h, _ = login("anu")
    assert client.get("/settings/lead-statuses", headers=anu_h).status_code == 200
    assert client.post("/settings/lead-statuses", headers=manager_h, json={"name": "Visit Done"}).status_code == 403
    assert client.post("/settings/lead-statuses", headers=anu_h, json={"name": "Visit Done"}).status_code == 403
    created = client.post("/settings/lead-statuses", headers=admin_h, json={"name": "Visit Done"})
    assert created.status_code == 201 and created.json()["slug"] == "visit_done"


def test_status_names_are_case_insensitive_unique(client, login):
    admin_h, _ = login("admin")
    assert client.post("/settings/lead-statuses", headers=admin_h, json={"name": "Hot Lead"}).status_code == 201
    assert client.post("/settings/lead-statuses", headers=admin_h, json={"name": "hot lead"}).status_code == 409


def test_status_cap_is_twenty_active(client, login):
    admin_h, _ = login("admin")
    # 4 seeded; add 16 more to reach 20
    for i in range(16):
        assert client.post("/settings/lead-statuses", headers=admin_h, json={"name": f"Stage {i}"}).status_code == 201
    assert client.post("/settings/lead-statuses", headers=admin_h, json={"name": "One too many"}).status_code == 400


def test_reactivating_a_status_respects_the_cap(client, login):
    admin_h, _ = login("admin")
    hidden = client.post("/settings/lead-statuses", headers=admin_h, json={"name": "Parked"}).json()
    assert client.patch(f"/settings/lead-statuses/{hidden['id']}", headers=admin_h, json={"is_active": False}).status_code == 200
    for i in range(16):  # 4 seeded + 16 = 20 active
        client.post("/settings/lead-statuses", headers=admin_h, json={"name": f"Stage {i}"})
    r = client.patch(f"/settings/lead-statuses/{hidden['id']}", headers=admin_h, json={"is_active": True})
    assert r.status_code == 400


def test_timeline_lists_record_logs_newest_first(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    raj_h, _ = login("raj")
    lead = _create_lead(client, admin_h, assigned_to=anu["id"])
    client.post(f"/leads/{lead['id']}/interactions/log", headers=anu_h, json={"activity_type": "note", "notes": "x"})
    task = _task(client, anu_h, lead["id"], anu["id"]).json()
    client.patch(f"/interactions/{task['id']}", headers=anu_h, json={"status": "completed", "outcome": "done"})
    rows = client.get(f"/leads/{lead['id']}/timeline", headers=anu_h).json()
    assert [r["action"] for r in rows][:3] == ["task_completed", "task_created", "activity_logged"]
    assert rows[0]["label"] == "Follow-up completed" and rows[0]["actor_name"] == "Anu Sales"
    assert rows[-1]["action"] == "created"
    assert client.get(f"/leads/{lead['id']}/timeline", headers=raj_h).status_code == 403


def test_timeline_records_status_and_assignee_changes_and_detail_has_assignee_name(client, login):
    admin_h, _ = login("admin")
    _, anu = login("anu")
    _, raj = login("raj")
    lead = _create_lead(client, admin_h, assigned_to=anu["id"])
    client.put(f"/leads/{lead['id']}", json={"status": "contacted"}, headers=admin_h)
    client.put(f"/leads/{lead['id']}", json={"assigned_to": raj["id"]}, headers=admin_h)
    client.put(f"/leads/{lead['id']}", json={"status": "contacted"}, headers=admin_h)  # no change → no row
    rows = client.get(f"/leads/{lead['id']}/timeline", headers=admin_h).json()
    assert [r["action"] for r in rows] == ["assigned", "status_changed", "created"]
    assert rows[1]["old_value"] == "new" and rows[1]["new_value"] == "contacted"
    assert client.get(f"/leads/{lead['id']}", headers=admin_h).json()["assigned_name"] == "Raj Sales"


def test_locked_statuses_cannot_be_deleted_or_deactivated(client, login):
    admin_h, _ = login("admin")
    rows = {r["slug"]: r for r in client.get("/settings/lead-statuses", headers=admin_h).json()}
    new_id = rows["new"]["id"]
    assert client.delete(f"/settings/lead-statuses/{new_id}", headers=admin_h).status_code == 400
    assert client.patch(f"/settings/lead-statuses/{new_id}", headers=admin_h, json={"is_active": False}).status_code == 400


def test_delete_status_in_use_returns_409(client, login):
    admin_h, _ = login("admin")
    rows = {r["slug"]: r for r in client.get("/settings/lead-statuses", headers=admin_h).json()}
    lead = _create_lead(client, admin_h)
    assert client.put(f"/leads/{lead['id']}", json={"status": "contacted"}, headers=admin_h).status_code == 200
    assert client.delete(f"/settings/lead-statuses/{rows['contacted']['id']}", headers=admin_h).status_code == 409
    assert client.delete(f"/settings/lead-statuses/{rows['lost']['id']}", headers=admin_h).status_code == 204


# ── Phase 6: WhatsApp inbox ──────────────────────────────────────────────────

def test_whatsapp_messages_scoped_by_role(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    raj_h, _ = login("raj")
    lead = _create_lead(client, admin_h, assigned_to=anu["id"])
    assert client.get(f"/leads/{lead['id']}/whatsapp-messages", headers=anu_h).json() == []
    assert client.get(f"/leads/{lead['id']}/whatsapp-messages", headers=raj_h).status_code == 403


def test_whatsapp_reply_validation(client, login):
    admin_h, _ = login("admin")
    lead = _create_lead(client, admin_h)
    assert client.post(f"/leads/{lead['id']}/whatsapp-reply", headers=admin_h, json={}).status_code == 400
    # No Meta credentials in the test environment
    assert client.post(f"/leads/{lead['id']}/whatsapp-reply", headers=admin_h, json={"message": "hi"}).status_code == 503


def test_staff_created_lead_is_theirs_and_staff_cannot_import(client, login):
    _, anu = login("anu")
    anu_h, _ = login("anu")
    r = client.post("/leads?allow_duplicate=false", json={"first_name": "Self", "phone": next(_PHONES)}, headers=anu_h)
    assert r.status_code == 201, r.text
    assert r.json()["assigned_to"] == anu["id"]

    csv = b"phone\n9830000002\n"
    imp = client.post("/leads/import/commit", headers=anu_h,
                      files={"file": ("leads.csv", csv, "text/csv")}, data={"mapping": '{"0": "phone"}'})
    assert imp.status_code == 403
    ana = client.post("/leads/import/analyze", headers=anu_h,
                      files={"file": ("leads.csv", csv, "text/csv")}, data={"mapping": '{"0": "phone"}'})
    assert ana.status_code == 403
