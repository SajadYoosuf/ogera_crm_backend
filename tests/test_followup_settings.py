"""GET / PATCH /settings/followups (docs/followups-tasks-prd.md, test T8 and validation)."""
import json

import pytest
from sqlalchemy import select

from conftest import SessionLocal
from models import RecordLog


@pytest.fixture(autouse=True)
def _heads_see_all(app):
    from models.role import Role
    with SessionLocal() as db:
        for role in db.scalars(select(Role).where(Role.slug.in_(("admin", "manager")))).all():
            role.see_all_records = True
        db.commit()


def test_anyone_signed_in_reads_defaults(client, login):
    anu_h, _ = login("anu")
    r = client.get("/settings/followups", headers=anu_h)
    assert r.status_code == 200
    body = r.json()
    assert body["default_due_time"] == "09:00" and body["morning_time"] == "10:00"
    assert body["reminder_mode"] == "due_day_only" and body["allow_staff_reschedule"] is True
    assert body["status_colors"] == {"todo": "gray", "inprogress": "blue", "completed": "teal"}
    assert client.get("/settings/followups").status_code == 401


def test_staff_cannot_save_manager_can_and_it_is_audited(client, login):
    anu_h, _ = login("anu")
    manager_h, manager = login("manager")
    change = {"default_due_time": "10:00", "evening_time": "18:00", "due_today_reminder_hour": 9,
              "reminder_mode": "daily_until_done", "reminder_max_days": 7}
    assert client.patch("/settings/followups", headers=anu_h, json=change).status_code == 403
    r = client.patch("/settings/followups", headers=manager_h, json=change)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["default_due_time"] == "10:00" and body["evening_time"] == "18:00"
    assert body["reminder_mode"] == "daily_until_done" and body["stored"] is True and body["updated_by"] == manager["id"]
    assert client.get("/settings/followups", headers=anu_h).json()["evening_time"] == "18:00"
    with SessionLocal() as db:
        log = db.scalar(select(RecordLog).where(RecordLog.action == "followup_settings_updated"))
    assert log is not None and log.actor_id == manager["id"]
    assert json.loads(log.new_value)["evening_time"] == "18:00" and json.loads(log.old_value)["evening_time"] == "17:00"


def test_unchanged_save_writes_no_audit_row(client, login):
    admin_h, _ = login("admin")
    client.patch("/settings/followups", headers=admin_h, json={"retry_followup_days": 4})
    client.patch("/settings/followups", headers=admin_h, json={"retry_followup_days": 4})
    with SessionLocal() as db:
        assert len(db.scalars(select(RecordLog).where(RecordLog.action == "followup_settings_updated")).all()) == 1


@pytest.mark.parametrize("body", [
    {"reminder_mode": "hourly"},
    {"due_today_reminder_hour": 24},
    {"morning_time": "25:00"},
    {"retry_followup_days": 0},
    {"status_colors": {"todo": "pink"}},
    {"status_colors": {"blocked": "red"}},
    {"unknown_field": 1},
    {},
])
def test_invalid_values_are_rejected(client, login, body):
    admin_h, _ = login("admin")
    r = client.patch("/settings/followups", headers=admin_h, json=body)
    assert r.status_code == 422, r.text
    assert isinstance(r.json()["detail"], str)


def test_status_colours_merge(client, login):
    admin_h, _ = login("admin")
    r = client.patch("/settings/followups", headers=admin_h, json={"status_colors": {"inprogress": "purple"}})
    assert r.json()["status_colors"] == {"todo": "gray", "inprogress": "purple", "completed": "teal"}
