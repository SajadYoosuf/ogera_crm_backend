"""Tasks page, completion log and reschedule (docs/followups-tasks-prd.md, tests T1 to T7, T9 to T13).

Runs against the in-memory SQLite app from conftest.py. Staff = anu, raj; heads = admin, manager.
"""
import datetime

import pytest
from sqlalchemy import select, text

from conftest import SessionLocal
from models import Interaction, LeadStatus
from business_time import ist_day_start_utc, today_ist

_PHONES = iter(f"97{n:08d}" for n in range(10_000_000, 99_999_999))
UTC = datetime.timezone.utc


def _iso(delta: datetime.timedelta) -> str:
    return (datetime.datetime.now(UTC) + delta).isoformat()


@pytest.fixture(autouse=True)
def _setup(app):
    from models.role import Role
    with SessionLocal() as db:
        for i, (slug, name) in enumerate([("new", "New"), ("contacted", "Contacted"), ("lost", "Lost")]):
            db.add(LeadStatus(slug=slug, name=name, sort_order=i, is_active=True))
        for role in db.scalars(select(Role).where(Role.slug.in_(("admin", "manager")))).all():
            role.see_all_records = True  # production sets this for heads
        db.commit()


def _lead(client, headers, assigned_to=None, **extra):
    body = {"first_name": "Task", "last_name": "Lead", "phone": next(_PHONES), **extra}
    if assigned_to is not None:
        body["assigned_to"] = assigned_to
    r = client.post("/leads?allow_duplicate=false", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _task(client, headers, lead_id, assigned_to, due_at=None, **extra):
    r = client.post(f"/leads/{lead_id}/interactions/task", headers=headers, json={
        "activity_type": "phone_call", "due_at": due_at or _iso(datetime.timedelta(days=2)),
        "assigned_to": assigned_to, **extra,
    })
    assert r.status_code == 201, r.text
    return r.json()


def _tasks(client, headers, **params):
    r = client.get("/lead-tasks", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _types(client, headers):
    return [n["type"] for n in client.get("/notifications", headers=headers).json()["items"]]


# ── T1 / T2: scope and filters ──────────────────────────────────────────────

def test_staff_sees_only_own_tasks_and_counts_match(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    raj_h, raj = login("raj")
    lead_a = _lead(client, admin_h, assigned_to=anu["id"])
    lead_r = _lead(client, admin_h, assigned_to=raj["id"])
    _task(client, anu_h, lead_a["id"], anu["id"])
    _task(client, raj_h, lead_r["id"], raj["id"])
    # A staff member cannot widen the scope with assigned_to.
    body = _tasks(client, anu_h, bucket="upcoming", assigned_to=raj["id"])
    assert [t["assigned_to"] for t in body["items"]] == [anu["id"]]
    assert body["summary"]["upcoming"] == 1 == body["total"]
    assert body["items"][0]["lead_name"] == "Task Lead"


def test_head_sees_all_and_filters_by_assignee(client, login):
    admin_h, _ = login("admin")
    manager_h, _ = login("manager")
    anu_h, anu = login("anu")
    raj_h, raj = login("raj")
    _task(client, anu_h, _lead(client, admin_h, assigned_to=anu["id"])["id"], anu["id"])
    _task(client, raj_h, _lead(client, admin_h, assigned_to=raj["id"])["id"], raj["id"])
    assert _tasks(client, manager_h, bucket="upcoming")["summary"]["upcoming"] == 2
    only_anu = _tasks(client, manager_h, bucket="upcoming", assigned_to=anu["id"])
    assert only_anu["summary"]["upcoming"] == 1 and only_anu["items"][0]["assigned_to"] == anu["id"]


def test_search_matches_lead_name_or_phone(client, login):
    admin_h, admin = login("admin")
    lead = _lead(client, admin_h, first_name="Zebulon")
    _task(client, admin_h, lead["id"], admin["id"])
    _task(client, admin_h, _lead(client, admin_h)["id"], admin["id"])
    assert _tasks(client, admin_h, bucket="upcoming", q="zebu")["total"] == 1
    assert _tasks(client, admin_h, bucket="upcoming", q=lead["phone"][-6:])["total"] == 1


def test_deleted_leads_are_left_out(client, login):
    admin_h, admin = login("admin")
    lead = _lead(client, admin_h)
    _task(client, admin_h, lead["id"], admin["id"])
    assert client.delete(f"/leads/{lead['id']}", headers=admin_h).status_code in (200, 204)
    assert _tasks(client, admin_h, bucket="upcoming")["total"] == 0


# ── T3: overdue by the hour ─────────────────────────────────────────────────

def test_task_one_hour_late_is_overdue(client, login):
    admin_h, admin = login("admin")
    task = _task(client, admin_h, _lead(client, admin_h)["id"], admin["id"], due_at=_iso(-datetime.timedelta(hours=1)))
    assert task["is_overdue"] is True and 59 <= task["overdue_minutes"] <= 61 and task["overdue_days"] == 0
    body = _tasks(client, admin_h, bucket="overdue")
    assert [t["id"] for t in body["items"]] == [task["id"]]


def test_buckets(client, login):
    admin_h, admin = login("admin")
    lead = _lead(client, admin_h)
    today_late = (ist_day_start_utc(today_ist()) + datetime.timedelta(hours=23, minutes=59)).replace(tzinfo=UTC)
    in_today = _task(client, admin_h, lead["id"], admin["id"], due_at=today_late.isoformat())
    old = _task(client, admin_h, lead["id"], admin["id"], due_at=_iso(-datetime.timedelta(days=3)))
    later = _task(client, admin_h, lead["id"], admin["id"], due_at=_iso(datetime.timedelta(days=5)))
    done = _task(client, admin_h, lead["id"], admin["id"])
    client.patch(f"/interactions/{done['id']}", headers=admin_h, json={"status": "completed", "outcome": "done", "notes": "ok"})

    ids = lambda b: [t["id"] for t in _tasks(client, admin_h, bucket=b)["items"]]  # noqa: E731
    assert in_today["id"] in ids("today") and old["id"] not in ids("today")
    assert ids("overdue") == [old["id"]]
    assert ids("upcoming") == [later["id"]]
    assert ids("completed") == [done["id"]]
    summary = _tasks(client, admin_h)["summary"]
    assert summary["completed"] == 1 and summary["overdue"] == 1


# ── T4 / T5: completion writes a linked activity ────────────────────────────

def test_complete_call_as_whatsapp_writes_linked_log(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    lead = _lead(client, admin_h, assigned_to=anu["id"])
    task = _task(client, anu_h, lead["id"], anu["id"])
    r = client.patch(f"/interactions/{task['id']}", headers=anu_h, json={
        "status": "completed", "outcome": "no_answer", "notes": "no reply on WhatsApp", "actual_activity_type": "whatsapp",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["activity_type"] == "phone_call"  # the plan is kept
    assert body["actual_activity_type"] == "whatsapp" and body["completed_at"]
    logs = client.get(f"/leads/{lead['id']}/interactions", headers=anu_h, params={"kind": "log"}).json()
    assert len(logs) == 1
    assert logs[0]["parent_task_id"] == task["id"] and logs[0]["activity_type"] == "whatsapp"
    assert logs[0]["outcome"] == "no_answer" and logs[0]["done_by"] == anu["id"]
    timeline = client.get(f"/leads/{lead['id']}/timeline", headers=anu_h).json()
    completed = [e for e in timeline if e["action"] == "task_completed"]
    assert len(completed) == 1 and completed[0]["label"] == "Follow-up completed"
    assert completed[0]["detail"].lower() == "done via whatsapp"


def test_complete_defaults_to_planned_type(client, login):
    admin_h, admin = login("admin")
    lead = _lead(client, admin_h)
    task = _task(client, admin_h, lead["id"], admin["id"])
    body = client.patch(f"/interactions/{task['id']}", headers=admin_h, json={"status": "completed", "outcome": "done"}).json()
    assert body["actual_activity_type"] == "phone_call"


def test_complete_with_other_needs_a_label(client, login):
    admin_h, admin = login("admin")
    task = _task(client, admin_h, _lead(client, admin_h)["id"], admin["id"])
    url = f"/interactions/{task['id']}"
    bad = client.patch(url, headers=admin_h, json={"status": "completed", "outcome": "done", "actual_activity_type": "other"})
    assert bad.status_code == 422 and "Other" in bad.json()["detail"]
    assert client.get("/lead-tasks", headers=admin_h, params={"bucket": "upcoming"}).json()["total"] == 1  # still open
    ok = client.patch(url, headers=admin_h, json={"status": "completed", "outcome": "done", "actual_activity_type": "other", "custom_label": "Site visit"})
    assert ok.status_code == 200 and ok.json()["actual_custom_label"] == "Site visit"


def test_completing_twice_is_rejected_and_writes_one_log(client, login):
    admin_h, admin = login("admin")
    lead = _lead(client, admin_h)
    task = _task(client, admin_h, lead["id"], admin["id"])
    done = {"status": "completed", "outcome": "done"}
    assert client.patch(f"/interactions/{task['id']}", headers=admin_h, json=done).status_code == 200
    assert client.patch(f"/interactions/{task['id']}", headers=admin_h, json=done).status_code == 400
    assert len(client.get(f"/leads/{lead['id']}/interactions", headers=admin_h, params={"kind": "log"}).json()) == 1


# ── T6 / T7: reschedule ─────────────────────────────────────────────────────

def test_reschedule_to_the_past_is_rejected(client, login):
    admin_h, admin = login("admin")
    lead = _lead(client, admin_h)
    task = _task(client, admin_h, lead["id"], admin["id"])
    r = client.patch(f"/leads/{lead['id']}/interactions/{task['id']}/reschedule", headers=admin_h,
                     json={"due_at": _iso(-datetime.timedelta(hours=3))})
    assert r.status_code == 422 and "past" in r.json()["detail"]
    after = client.get(f"/leads/{lead['id']}/interactions", headers=admin_h, params={"kind": "task"}).json()[0]
    assert after["due_at"] == task["due_at"]


def test_reschedule_records_reason_and_notifies_assignee_when_someone_else_moves_it(client, login):
    admin_h, _ = login("admin")
    manager_h, _ = login("manager")
    anu_h, anu = login("anu")
    lead = _lead(client, admin_h, assigned_to=anu["id"])
    task = _task(client, anu_h, lead["id"], anu["id"])
    url = f"/leads/{lead['id']}/interactions/{task['id']}/reschedule"
    # The assignee moving their own task is not notified.
    assert client.patch(url, headers=anu_h, json={"due_at": _iso(datetime.timedelta(days=3))}).status_code == 200
    assert "followup_rescheduled" not in _types(client, anu_h)
    r = client.patch(url, headers=manager_h, json={"due_at": _iso(datetime.timedelta(days=4)), "reason": "client travelling"})
    assert r.status_code == 200, r.text
    assert _types(client, anu_h).count("followup_rescheduled") == 1
    event = next(e for e in client.get(f"/leads/{lead['id']}/timeline", headers=admin_h).json() if e["action"] == "task_rescheduled")
    assert event["detail"].startswith("Moved from ") and event["detail"].endswith("client travelling")


def test_completed_task_cannot_be_rescheduled(client, login):
    admin_h, admin = login("admin")
    lead = _lead(client, admin_h)
    task = _task(client, admin_h, lead["id"], admin["id"])
    client.patch(f"/interactions/{task['id']}", headers=admin_h, json={"status": "completed", "outcome": "done"})
    r = client.patch(f"/leads/{lead['id']}/interactions/{task['id']}/reschedule", headers=admin_h,
                     json={"due_at": _iso(datetime.timedelta(days=1))})
    assert r.status_code == 400


# ── T9: staff permissions from settings ─────────────────────────────────────

def test_staff_flags_block_reschedule_and_create(client, login):
    manager_h, _ = login("manager")
    anu_h, anu = login("anu")
    admin_h, admin = login("admin")
    lead = _lead(client, admin_h, assigned_to=anu["id"])
    task = _task(client, anu_h, lead["id"], anu["id"])
    r = client.patch("/settings/followups", headers=manager_h,
                     json={"allow_staff_reschedule": False, "allow_staff_create_followup": False})
    assert r.status_code == 200, r.text
    url = f"/leads/{lead['id']}/interactions/{task['id']}/reschedule"
    assert client.patch(url, headers=anu_h, json={"due_at": _iso(datetime.timedelta(days=3))}).status_code == 403
    assert client.post(f"/leads/{lead['id']}/interactions/task", headers=anu_h, json={
        "activity_type": "phone_call", "due_at": _iso(datetime.timedelta(days=1)), "assigned_to": anu["id"],
    }).status_code == 403
    # Heads are never blocked.
    assert client.patch(url, headers=manager_h, json={"due_at": _iso(datetime.timedelta(days=3))}).status_code == 200
    _task(client, admin_h, lead["id"], admin["id"])


# ── T10 / T11: reminder modes ───────────────────────────────────────────────

def _set(client, headers, **values):
    r = client.patch("/settings/followups", headers=headers, json=values)
    assert r.status_code == 200, r.text


def test_daily_mode_sends_one_digest_per_day_until_done(client, login):
    from services.followup_reminders import send_due_today

    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    _set(client, admin_h, reminder_mode="daily_until_done", reminder_max_days=7)
    lead = _lead(client, admin_h, assigned_to=anu["id"])
    due_today = (ist_day_start_utc(today_ist()) + datetime.timedelta(hours=10)).replace(tzinfo=UTC).isoformat()
    task = _task(client, anu_h, lead["id"], anu["id"], due_at=due_today)
    _task(client, anu_h, lead["id"], anu["id"], due_at=due_today)

    with SessionLocal() as db:
        assert send_due_today(db) == 1
        assert send_due_today(db) == 0  # one per day
        # Three days later both are still open: one digest that day too.
        assert send_due_today(db, today_ist() + datetime.timedelta(days=3)) == 1
    digests = [n for n in client.get("/notifications", headers=anu_h).json()["items"] if n["type"] == "followup_digest"]
    assert len(digests) == 2
    assert any("2 due today" in n["message"] for n in digests)
    assert any("2 overdue" in n["message"] for n in digests)

    for t in client.get(f"/leads/{lead['id']}/interactions", headers=anu_h, params={"kind": "task"}).json():
        client.patch(f"/interactions/{t['id']}", headers=anu_h, json={"status": "completed", "outcome": "done"})
    with SessionLocal() as db:
        assert send_due_today(db, today_ist() + datetime.timedelta(days=4)) == 0  # done: no more digests
    assert task


def test_daily_mode_respects_max_days(client, login):
    from services.followup_reminders import send_due_today

    admin_h, admin = login("admin")
    _set(client, admin_h, reminder_mode="daily_until_done", reminder_max_days=2)
    _task(client, admin_h, _lead(client, admin_h)["id"], admin["id"], due_at=_iso(-datetime.timedelta(days=5)))
    with SessionLocal() as db:
        assert send_due_today(db) == 0


def test_due_day_only_mode_is_unchanged(client, login):
    from services.followup_reminders import send_due_today

    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    lead = _lead(client, admin_h, assigned_to=anu["id"])
    due = (ist_day_start_utc(today_ist()) + datetime.timedelta(hours=10)).replace(tzinfo=UTC).isoformat()
    _task(client, anu_h, lead["id"], anu["id"], due_at=due)
    _task(client, anu_h, lead["id"], anu["id"], due_at=_iso(-datetime.timedelta(days=2)))  # overdue: not in due-day mode
    with SessionLocal() as db:
        assert send_due_today(db) == 1
    assert _types(client, anu_h).count("followup_due") == 1
    assert "followup_digest" not in _types(client, anu_h)


def test_scheduled_reminder_runs_only_in_the_configured_hour(client, login):
    from services.followup_reminders import send_scheduled_daily_reminder

    admin_h, admin = login("admin")
    _set(client, admin_h, due_today_reminder_hour=7)
    due = (ist_day_start_utc(today_ist()) + datetime.timedelta(hours=10)).replace(tzinfo=UTC).isoformat()
    _task(client, admin_h, _lead(client, admin_h)["id"], admin["id"], due_at=due)
    day = datetime.datetime.combine(today_ist(), datetime.time(9, 0))
    with SessionLocal() as db:
        assert send_scheduled_daily_reminder(db, day) == 0
        assert send_scheduled_daily_reminder(db, day.replace(hour=7)) == 1


def test_reminders_switched_off(client, login):
    from services.followup_reminders import send_due_now, send_due_today, send_overdue

    admin_h, admin = login("admin")
    _set(client, admin_h, reminders_enabled=False)
    _task(client, admin_h, _lead(client, admin_h)["id"], admin["id"], due_at=_iso(-datetime.timedelta(minutes=2)))
    with SessionLocal() as db:
        assert send_due_today(db) == send_overdue(db) == send_due_now(db) == 0


# ── T12: settings table missing ─────────────────────────────────────────────

def test_app_runs_on_defaults_without_the_settings_table(client, login):
    from services.followup_reminders import send_due_now

    with SessionLocal() as db:
        db.execute(text("DROP TABLE followup_settings"))
        db.commit()
    admin_h, admin = login("admin")
    anu_h, anu = login("anu")
    settings = client.get("/settings/followups", headers=anu_h)
    assert settings.status_code == 200
    assert settings.json()["stored"] is False and settings.json()["default_due_time"] == "09:00"
    lead = _lead(client, admin_h, assigned_to=anu["id"])
    task = _task(client, anu_h, lead["id"], anu["id"], due_at=_iso(-datetime.timedelta(minutes=2)))
    assert client.patch(f"/leads/{lead['id']}/interactions/{task['id']}/reschedule", headers=anu_h,
                        json={"due_at": _iso(datetime.timedelta(days=1))}).status_code == 200
    _task(client, anu_h, lead["id"], anu["id"], due_at=_iso(-datetime.timedelta(minutes=2)))
    with SessionLocal() as db:
        assert send_due_now(db) == 1
    assert client.patch("/settings/followups", headers=admin_h, json={"retry_followup_days": 2}).status_code == 503
    with SessionLocal() as db:
        assert db.scalar(select(Interaction.id).limit(1)) is not None


# ── T13: task status labels ─────────────────────────────────────────────────

def test_task_status_labels_can_be_renamed_not_added_or_removed(client, login):
    import settings_catalog

    with SessionLocal() as db:
        settings_catalog.seed_defaults(db)
    admin_h, _ = login("admin")
    statuses = client.get("/settings/enums/task_status", headers=admin_h).json()
    assert [s["value"] for s in statuses] == ["todo", "inprogress", "completed"]
    inprogress = next(s for s in statuses if s["value"] == "inprogress")
    assert inprogress["locked"] is True
    renamed = client.patch(f"/settings/enums/task_status/{inprogress['id']}", headers=admin_h, json={"label": "Working"})
    assert renamed.status_code == 200 and renamed.json()["value"] == "inprogress" and renamed.json()["label"] == "Working"
    assert client.post("/settings/enums/task_status", headers=admin_h, json={"label": "Blocked"}).status_code == 400
    assert client.delete(f"/settings/enums/task_status/{inprogress['id']}", headers=admin_h).status_code == 400
    assert client.patch(f"/settings/enums/task_status/{inprogress['id']}", headers=admin_h, json={"is_active": False}).status_code == 400
