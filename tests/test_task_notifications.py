"""Follow-up event notifications: create, status change, reschedule, reassign, delete.

Staff = anu, raj; heads = admin, manager. Runs against the in-memory SQLite app from conftest.py.
"""
import datetime

import pytest
from sqlalchemy import select

from conftest import SessionLocal
from models import LeadStatus

_PHONES = iter(f"96{n:08d}" for n in range(10_000_000, 99_999_999))
UTC = datetime.timezone.utc


@pytest.fixture(autouse=True)
def _setup(app):
    from models.role import Role
    with SessionLocal() as db:
        for i, (slug, name) in enumerate([("new", "New"), ("contacted", "Contacted"), ("lost", "Lost")]):
            db.add(LeadStatus(slug=slug, name=name, sort_order=i, is_active=True))
        for role in db.scalars(select(Role).where(Role.slug.in_(("admin", "manager")))).all():
            role.see_all_records = True
        db.commit()


def _lead(client, headers, assigned_to):
    body = {"first_name": "Notify", "last_name": "Lead", "phone": next(_PHONES), "assigned_to": assigned_to}
    r = client.post("/leads?allow_duplicate=false", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _due(days: int = 2) -> str:
    return (datetime.datetime.now(UTC) + datetime.timedelta(days=days)).isoformat()


def _task(client, headers, lead_id, assigned_to):
    r = client.post(f"/leads/{lead_id}/interactions/task", headers=headers, json={
        "activity_type": "phone_call", "title": "Call back", "due_at": _due(),
        "assigned_to": assigned_to,
    })
    assert r.status_code == 201, r.text
    return r.json()


def _types(client, headers):
    return [n["type"] for n in client.get("/notifications", headers=headers).json()["items"]]


def _followups(client, headers):
    return [t for t in _types(client, headers) if t.startswith("followup")]


def test_staff_creating_own_task_notifies_only_heads(client, login):
    admin_h, _ = login("admin")
    manager_h, _ = login("manager")
    anu_h, anu = login("anu")
    lead = _lead(client, admin_h, anu["id"])
    _task(client, anu_h, lead["id"], anu["id"])
    assert _types(client, admin_h).count("followup_created") == 1
    assert _types(client, manager_h).count("followup_created") == 1
    assert _followups(client, anu_h) == []


def test_head_assigning_task_notifies_assignee_and_other_head(client, login):
    admin_h, _ = login("admin")
    manager_h, _ = login("manager")
    anu_h, anu = login("anu")
    lead = _lead(client, admin_h, anu["id"])
    _task(client, admin_h, lead["id"], anu["id"])
    assert "followup_assigned" in _types(client, anu_h)
    assert "followup_created" in _types(client, manager_h)
    assert _followups(client, admin_h) == []


def test_staff_status_change_notifies_heads_not_self(client, login):
    admin_h, _ = login("admin")
    manager_h, _ = login("manager")
    anu_h, anu = login("anu")
    lead = _lead(client, admin_h, anu["id"])
    task = _task(client, anu_h, lead["id"], anu["id"])
    r = client.patch(f"/interactions/{task['id']}", headers=anu_h, json={"status": "inprogress"})
    assert r.status_code == 200, r.text
    assert "followup_status" in _types(client, admin_h)
    assert "followup_status" in _types(client, manager_h)
    assert "followup_status" not in _types(client, anu_h)


def test_head_status_change_notifies_assignee(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    lead = _lead(client, admin_h, anu["id"])
    task = _task(client, admin_h, lead["id"], anu["id"])
    r = client.patch(f"/interactions/{task['id']}", headers=admin_h, json={"status": "inprogress"})
    assert r.status_code == 200, r.text
    assert "followup_status" in _types(client, anu_h)
    assert "followup_status" not in _types(client, admin_h)


def test_reschedule_notifies_heads_and_assignee_except_actor(client, login):
    admin_h, _ = login("admin")
    manager_h, _ = login("manager")
    anu_h, anu = login("anu")
    lead = _lead(client, admin_h, anu["id"])
    task = _task(client, anu_h, lead["id"], anu["id"])
    r = client.patch(f"/leads/{lead['id']}/interactions/{task['id']}/reschedule", headers=anu_h,
                     json={"due_at": _due(5)})
    assert r.status_code == 200, r.text
    assert "followup_rescheduled" in _types(client, admin_h)
    assert "followup_rescheduled" in _types(client, manager_h)
    assert "followup_rescheduled" not in _types(client, anu_h)


def test_reassign_notifies_new_old_and_other_head(client, login):
    admin_h, _ = login("admin")
    manager_h, _ = login("manager")
    anu_h, anu = login("anu")
    raj_h, raj = login("raj")
    lead = _lead(client, admin_h, anu["id"])
    task = _task(client, admin_h, lead["id"], anu["id"])
    r = client.patch(f"/leads/{lead['id']}/interactions/{task['id']}/reassign", headers=admin_h,
                     json={"assigned_to": raj["id"]})
    assert r.status_code == 200, r.text
    assert "followup_assigned" in _types(client, raj_h)
    assert "followup_reassigned" in _types(client, anu_h)
    assert "followup_reassigned" in _types(client, manager_h)
    assert "followup_reassigned" not in _types(client, raj_h)
    assert _followups(client, admin_h) == []


def test_delete_notifies_assignee_and_other_head(client, login):
    admin_h, _ = login("admin")
    manager_h, _ = login("manager")
    anu_h, anu = login("anu")
    lead = _lead(client, admin_h, anu["id"])
    task = _task(client, admin_h, lead["id"], anu["id"])
    r = client.delete(f"/leads/{lead['id']}/interactions/{task['id']}", headers=admin_h)
    assert r.status_code == 204, r.text
    assert "followup_deleted" in _types(client, anu_h)
    assert "followup_deleted" in _types(client, manager_h)
    assert _followups(client, admin_h) == []
