"""Partners move through their own pipeline; consumers keep theirs."""
from database import SessionLocal
from models import StageType


def _contact(client, headers, sources, **overrides):
    body = {
        "contact_type": "consumer", "name": "Arun Kumar", "phone": "9876543210", "district": "Calicut",
        "address": "House 4, MG Road", "lead_source_id": sources("meta"),
        "consumer_profile": {"house_stage": "ready"},
    }
    body.update(overrides)
    response = client.post("/contacts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _partner(client, headers, sources):
    return _contact(client, headers, sources, contact_type="partner", name="Plumber Raju", phone="9222222222",
                    consumer_profile=None, partner_profile={"profession_type": "plumber", "work_area": "Calicut"})


def _stage_id(slug: str, contact_type: str) -> int:
    with SessionLocal() as db:
        row = db.query(StageType).filter(StageType.slug == slug, StageType.contact_type == contact_type).first()
        assert row is not None, f"Stage '{slug}' for type '{contact_type}' not seeded"
        return row.id


def _move(client, headers, admin, contact_id, stage_id):
    return client.post(f"/contacts/{contact_id}/stages", headers=headers,
                       json={"stage_id": stage_id, "changed_by": admin["id"]})


def test_partner_walks_its_own_pipeline(client, login, lead_source_id):
    headers, admin = login("admin")
    partner = _partner(client, headers, lead_source_id)
    for stage in ("connected", "demo_introduction", "aware", "interested", "active", "referral", "inactive"):
        sid = _stage_id(stage, "partner")
        assert _move(client, headers, admin, partner["id"], sid).status_code == 201, stage


def test_partner_cannot_use_consumer_only_stages(client, login, lead_source_id):
    headers, admin = login("admin")
    partner = _partner(client, headers, lead_source_id)
    for stage in ("quotation", "won", "after_sales_referring"):
        sid = _stage_id(stage, "consumer")
        response = _move(client, headers, admin, partner["id"], sid)
        assert response.status_code == 400, stage
        assert "partner pipeline" in response.json()["detail"]


def test_consumer_cannot_use_partner_only_stages(client, login, lead_source_id):
    headers, admin = login("admin")
    consumer = _contact(client, headers, lead_source_id)
    for stage in ("demo_introduction", "active", "inactive"):
        sid = _stage_id(stage, "partner")
        response = _move(client, headers, admin, consumer["id"], sid)
        assert response.status_code == 400, stage
        assert "consumer pipeline" in response.json()["detail"]
    assert _move(client, headers, admin, consumer["id"], _stage_id("first_call", "consumer")).status_code == 201


def test_partner_stages_do_not_appear_on_the_consumer_board(client, login):
    headers, _ = login("anu")
    slugs = [s["slug"] for s in client.get("/contacts/stage-types?contact_type=consumer", headers=headers).json()]
    assert not {"demo_introduction", "active", "inactive"} & set(slugs)


def test_tasks_filter_by_consumer_or_partner_and_list_all_dates_when_none_given(client, login, lead_source_id):
    import datetime
    headers, anu = login("anu")
    consumer = _contact(client, headers, lead_source_id)
    partner = _partner(client, headers, lead_source_id)
    today = datetime.date.today()
    for contact, day in ((consumer, today), (partner, today + datetime.timedelta(days=90))):
        due_at = datetime.datetime.combine(day, datetime.time(12, 0))
        if due_at < datetime.datetime.now():
            due_at = datetime.datetime.now() + datetime.timedelta(hours=1)
        response = client.post(f"/contacts/{contact['id']}/followups", headers=headers, json={
            "channel": "call", "due_at": due_at.isoformat(), "assigned_to": anu["id"]})
        assert response.status_code == 201, response.text

    def names(**params):
        body = client.get("/tasks", headers=headers, params=params).json()
        return sorted(item["contact_name"] for item in body["items"]), body["summary"]

    everyone, _ = names()  # no dates at all: every open task (includes auto-followups)
    assert set(everyone) == {"Arun Kumar", "Plumber Raju"}
    assert "Arun Kumar" in names(contact_type="consumer")[0]
    assert "Plumber Raju" in names(contact_type="partner")[0]
    assert client.get("/tasks", headers=headers, params={"contact_type": "dealer"}).status_code == 200


def test_tasks_status_due_today_and_overdue_ignore_the_date_range(client, login, lead_source_id, app):
    import datetime
    from database import SessionLocal
    from models.followup_schedule import FollowupSchedule
    headers, anu = login("anu")
    contact = _contact(client, headers, lead_source_id)
    today = datetime.date.today()
    ids = {}
    for key, day in (("today", today), ("later", today + datetime.timedelta(days=5))):
        due_at = datetime.datetime.combine(day, datetime.time(12, 0))
        if due_at < datetime.datetime.now():
            due_at = datetime.datetime.now() + datetime.timedelta(minutes=10)
        response = client.post(f"/contacts/{contact['id']}/followups", headers=headers, json={
            "channel": "call", "due_at": due_at.isoformat(), "assigned_to": anu["id"], "notes": key})
        assert response.status_code == 201, response.text
        ids[key] = response.json()["id"]
    # the API refuses a date in the past, so an old task is written straight to the database
    with SessionLocal() as db:
        db.add(FollowupSchedule(contact_id=contact["id"], channel="call", notes="old", status="pending",
                                assigned_to=anu["id"],
                                due_at=datetime.datetime.combine(today - datetime.timedelta(days=3), datetime.time(0, 0))))
        db.commit()

    def notes(**params):
        return sorted(item["notes"] for item in client.get("/tasks", headers=headers, params=params).json()["items"])

    assert notes(status="overdue") == ["old"]
    assert notes(status="due_today", date_from=(today + datetime.timedelta(days=5)).isoformat(), date_to=(today + datetime.timedelta(days=5)).isoformat()) == ["today"]
    assert set(notes(status="open")) >= {"later", "old", "today"}
    summary = client.get("/tasks", headers=headers, params={"status": "overdue"}).json()["summary"]
    assert summary["overdue"] == 1 and summary["today"] == 1
