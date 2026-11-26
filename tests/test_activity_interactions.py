"""Reminders, the daily digest, contact import and usage counts all work from the `interactions` table."""
import datetime
from datetime import timezone

import contact_import
import scheduler
import settings_catalog as catalog
from business_time import ist_day_bounds, today_ist
from database import SessionLocal
from models import Contact, Employee, Interaction, Notification
from services import followup_reminders
from services.notification_service import run_48h_rule, run_daily_digest


def _now():
    return datetime.datetime.now(timezone.utc)


def _contact(db, name, phone, owner_id, deleted=False):
    row = Contact(name=name, phone=phone, contact_type="consumer", assigned_to=owner_id,
                  deleted_at=_now() if deleted else None)
    db.add(row)
    db.flush()
    return row


def _interaction(db, contact_id, kind="log", hours_ago=1, activity_type="phone_call", entity_type="contact"):
    db.add(Interaction(entity_type=entity_type, entity_id=contact_id, kind=kind, activity_type=activity_type,
                       logged_at=_now() - datetime.timedelta(hours=hours_ago)))


def _owner(db):
    return db.query(Employee).filter(Employee.email.like("anu%")).one().id


def test_only_contacts_without_a_recent_log_get_an_inactivity_reminder(client):
    with SessionLocal() as db:
        owner = _owner(db)
        fresh = _contact(db, "Fresh", "9000000001", owner)
        stale = _contact(db, "Stale", "9000000002", owner)
        task_only = _contact(db, "Task only", "9000000003", owner)
        lead_log = _contact(db, "Same id as a lead", "9000000004", owner)
        _interaction(db, fresh.id, hours_ago=1)
        _interaction(db, stale.id, hours_ago=100)
        _interaction(db, task_only.id, kind="task", hours_ago=1)
        _interaction(db, lead_log.id, hours_ago=1, entity_type="lead")  # a lead's activity must not count
        db.commit()

        assert run_48h_rule(db) > 0

        reminded = {n.entity_id for n in db.query(Notification).filter(
            Notification.type == "inactivity", Notification.recipient_id == owner).all()}
        assert reminded == {stale.id, task_only.id, lead_log.id}


def test_activity_type_usage_counts_logs_on_live_records(client):
    with SessionLocal() as db:
        owner = _owner(db)
        live = _contact(db, "Live", "9000000011", owner)
        gone = _contact(db, "Gone", "9000000012", owner, deleted=True)
        _interaction(db, live.id, activity_type="demo")
        _interaction(db, live.id, activity_type="demo")
        _interaction(db, live.id, kind="task", activity_type="demo")
        _interaction(db, gone.id, activity_type="demo")
        db.commit()

        assert catalog.usage_counts(db, catalog.ACTIVITY_TYPE) == {"demo": 2}


def _task(db, entity_id, owner_id, due_at, status="todo", entity_type="contact"):
    db.add(Interaction(entity_type=entity_type, entity_id=entity_id, kind="task", activity_type="phone_call",
                       status=status, assigned_to=owner_id, due_at=due_at, logged_at=_now()))


def test_daily_digest_counts_open_tasks_due_today_on_live_records(client):
    start, end = ist_day_bounds(today_ist())
    with SessionLocal() as db:
        owner = _owner(db)
        live = _contact(db, "Live", "9000000021", owner)
        gone = _contact(db, "Gone", "9000000022", owner, deleted=True)
        _task(db, live.id, owner, start)  # first moment of the IST day
        _task(db, live.id, owner, end - datetime.timedelta(minutes=1), status="inprogress")
        _task(db, live.id, owner, start - datetime.timedelta(minutes=1))  # yesterday
        _task(db, live.id, owner, end)  # tomorrow
        _task(db, live.id, owner, start, status="completed")
        _task(db, gone.id, owner, start)
        db.commit()

        assert run_daily_digest(db) == 1

        digest = db.query(Notification).filter(
            Notification.type == "daily_digest", Notification.recipient_id == owner).one()
        assert digest.title == "You have 2 tasks today"


def test_contact_import_schedules_a_first_call_task(client):
    with SessionLocal() as db:
        owner = _owner(db)
        catalog.seed_defaults(db)  # the new_lead pipeline stages the import needs
        rows = [["name", "phone"], ["Imported One", "9000000031"], ["Imported Two", "9000000032"]]
        options = contact_import.Options()
        results = contact_import.analyze(db, rows, 0, {0: "name", 1: "phone"}, options)

        assert contact_import.run_import(db, results, options, [owner], created_by=owner)["created"] == 2

        ids = {c.id for c in db.query(Contact).filter(Contact.name.like("Imported%"))}
        tasks = db.query(Interaction).filter(Interaction.entity_type == "contact", Interaction.entity_id.in_(ids)).all()
        assert {t.entity_id for t in tasks} == ids
        assert {(t.kind, t.status, t.activity_type, t.assigned_to) for t in tasks} == {("task", "todo", "phone_call", owner)}


def test_scheduled_jobs_run_once_per_active_tenant(client, monkeypatch):
    ran = []
    monkeypatch.setattr(followup_reminders, "run_for_all_tenants", lambda job: ran.append(job.__name__))
    scheduler._run_inactivity_check()
    scheduler._run_daily_digest()
    assert ran == ["run_48h_rule", "run_daily_digest"]
