"""Partners move through their own pipeline; consumers keep theirs."""


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


def _move(client, headers, admin, contact, stage):
    return client.post(f"/contacts/{contact['id']}/stages", headers=headers, json={"stage": stage, "changed_by": admin["id"]})


def test_partner_walks_its_own_pipeline(client, login, lead_source_id):
    headers, admin = login("admin")
    partner = _partner(client, headers, lead_source_id)
    for stage in ("connected", "demo_introduction", "aware", "interested", "active_referral", "inactive"):
        assert _move(client, headers, admin, partner, stage).status_code == 201, stage


def test_partner_cannot_use_consumer_only_stages(client, login, lead_source_id):
    headers, admin = login("admin")
    partner = _partner(client, headers, lead_source_id)
    for stage in ("first_call", "quoted", "negotiating", "won", "repeat_customer"):
        response = _move(client, headers, admin, partner, stage)
        assert response.status_code == 400, stage
        assert "partner pipeline" in response.json()["detail"]


def test_consumer_cannot_use_partner_only_stages(client, login, lead_source_id):
    headers, admin = login("admin")
    consumer = _contact(client, headers, lead_source_id)
    for stage in ("demo_introduction", "active_referral", "inactive"):
        response = _move(client, headers, admin, consumer, stage)
        assert response.status_code == 400, stage
        assert "only for partners" in response.json()["detail"]
    assert _move(client, headers, admin, consumer, "first_call").status_code == 201


def test_partner_stages_do_not_appear_on_the_consumer_board(client, login):
    headers, _ = login("anu")
    ids = [s["id"] for s in client.get("/contacts/stage-types", headers=headers).json()]
    assert not {"demo_introduction", "active_referral", "inactive"} & set(ids)


def test_tasks_filter_by_consumer_or_partner_and_list_all_dates_when_none_given(client, login, lead_source_id):
    import datetime
    headers, anu = login("anu")
    consumer = _contact(client, headers, lead_source_id)
    partner = _partner(client, headers, lead_source_id)
    today = datetime.date.today()
    for contact, day in ((consumer, today), (partner, today + datetime.timedelta(days=90))):
        response = client.post(f"/contacts/{contact['id']}/activities", headers=headers, json={
            "type": "phone_call", "scheduled_date": day.isoformat(), "assigned_to": anu["id"]})
        assert response.status_code == 201, response.text

    def names(**params):
        body = client.get("/tasks", headers=headers, params=params).json()
        return sorted(item["contact_name"] for item in body["items"]), body["summary"]

    everyone, _ = names()  # no dates at all: every open task
    assert everyone == ["Arun Kumar", "Plumber Raju"]
    assert names(contact_type="consumer")[0] == ["Arun Kumar"]
    assert names(contact_type="partner")[0] == ["Plumber Raju"]
    assert client.get("/tasks", headers=headers, params={"contact_type": "dealer"}).status_code == 422


def test_tasks_status_due_today_and_overdue_ignore_the_date_range(client, login, lead_source_id, app):
    import datetime
    from database import SessionLocal
    from models import LeadActivity
    headers, anu = login("anu")
    contact = _contact(client, headers, lead_source_id)
    today = datetime.date.today()
    ids = {}
    for key, day in (("today", today), ("later", today + datetime.timedelta(days=5))):
        response = client.post(f"/contacts/{contact['id']}/activities", headers=headers, json={
            "type": "phone_call", "scheduled_date": day.isoformat(), "assigned_to": anu["id"], "notes": key})
        assert response.status_code == 201, response.text
        ids[key] = response.json()["id"]
    # the API refuses a date in the past, so an old task is written straight to the database
    with SessionLocal() as db:
        db.add(LeadActivity(contact_id=contact["id"], type="phone_call", notes="old", status="pending", scheduled_date=today - datetime.timedelta(days=3),
                            assigned_to=anu["id"], created_by=anu["id"]))
        db.commit()

    def notes(**params):
        return sorted(item["notes"] for item in client.get("/tasks", headers=headers, params=params).json()["items"])

    assert notes(status="overdue") == ["old"]
    assert notes(status="due_today", date_from=(today + datetime.timedelta(days=5)).isoformat(), date_to=(today + datetime.timedelta(days=5)).isoformat()) == ["today"]
    assert notes(status="open") == ["later", "old", "today"]
    summary = client.get("/tasks", headers=headers, params={"status": "overdue"}).json()["summary"]
    assert summary["overdue"] == 1 and summary["today"] == 1
