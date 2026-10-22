"""Endpoints the mobile app depends on, exercised through the real app."""
from seed_data import MOBILE_APP
import datetime

from database import SessionLocal
from models import PipelineStage

TODAY = datetime.date.today()


def _stage_id(slug: str, contact_type: str = "consumer") -> int:
    with SessionLocal() as db:
        row = db.query(PipelineStage).filter(PipelineStage.slug == slug, PipelineStage.contact_type == contact_type).first()
        assert row is not None, f"Stage '{slug}' for '{contact_type}' not seeded"
        return row.id


def _contact(client, headers, sources, **overrides):
    body = {
        "contact_type": "consumer",
        "name": "Arun Kumar",
        "phone": "9876543210",
        "district": "Calicut",
        "address": "House 4, MG Road",
        "lead_source_id": sources("meta"),
        "consumer_profile": {"house_stage": "ready"},
    }
    body.update(overrides)
    response = client.post("/contacts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# --- auth ---------------------------------------------------------------------------------

def test_login_returns_token_and_employee(login):
    headers, employee = login("anu")
    assert employee["role"] == "sales_executive"
    assert employee["email"] == "anu@example.com"
    assert headers["Authorization"].startswith("Bearer ")


def test_login_rejects_wrong_password(client):
    response = client.post("/auth/login", json={"email": "anu@example.com", "password": "nope"}, headers=MOBILE_APP)
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_protected_routes_need_a_token(client):
    assert client.get("/contacts").status_code in (401, 403)
    assert client.get("/tasks").status_code in (401, 403)


# --- contacts: list, stages ------------------------------------------------------------

def test_list_rows_carry_house_stage_and_location(client, login, lead_source_id):
    headers, _ = login("admin")
    _contact(client, headers, lead_source_id)
    _contact(client, headers, lead_source_id, name="Beena", phone="9876500000",
             consumer_profile={"house_stage": "under_construction"})
    rows = {r["name"]: r for r in client.get("/contacts", headers=headers).json()["items"]}
    assert rows["Arun Kumar"]["house_stage"] == "ready"
    assert rows["Beena"]["house_stage"] == "under_construction"
    assert rows["Arun Kumar"]["latitude"] is None


def test_stage_types_are_ordered_and_skip_legacy_referral(client, login):
    headers, _ = login("anu")
    stages = client.get("/pipeline/stages?contact_type=consumer", headers=headers).json()
    slugs = [s["slug"] for s in stages]
    assert slugs[0] == "new_lead" and "referral" not in slugs
    assert [s["order_index"] for s in stages] == list(range(len(stages)))
    assert {"won", "lost"} <= set(slugs)


def test_stage_change_needs_verified_details(client, login, lead_source_id):
    headers, admin = login("admin")
    unverified = _contact(client, headers, lead_source_id, consumer_profile=None)
    blocked = client.post(f"/contacts/{unverified['id']}/stages",
                          json={"stage_id": _stage_id("first_call"), "changed_by": admin["id"]}, headers=headers)
    assert blocked.status_code == 409
    assert "house_stage" in blocked.json()["detail"]["missing_fields"]

    verified = _contact(client, headers, lead_source_id, name="Ok", phone="9111111111")
    moved = client.post(f"/contacts/{verified['id']}/stages",
                        json={"stage_id": _stage_id("first_call"), "changed_by": admin["id"]}, headers=headers)
    assert moved.status_code == 201


# --- staff directory ------------------------------------------------------------------------

def test_directory_is_open_to_staff(client, login):
    headers, _ = login("anu")
    directory = client.get("/employees/directory", headers=headers)
    assert directory.status_code == 200
    assert {"Ada Admin", "Anu Sales"} <= {e["name"] for e in directory.json()}
    assert set(directory.json()[0]) == {"id", "name", "role", "phone"}


def test_directory_hides_deactivated_staff(client, login):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    client.put(f"/employees/{raj['id']}", json={"is_active": False}, headers=admin_headers)
    names = [e["name"] for e in client.get("/employees/directory", headers=admin_headers).json()]
    assert "Raj Sales" not in names


# --- follow-up tasks ------------------------------------------------------------------------

def test_schedule_then_complete_a_followup(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _contact(client, headers, lead_source_id, assigned_to=anu["id"])
    # Contact auto-creates a followup, but schedule another one for testing
    scheduled = client.post(f"/contacts/{contact['id']}/followups", headers=headers, json={
        "channel": "call", "due_at": (datetime.datetime.now() + datetime.timedelta(hours=2)).isoformat(), "assigned_to": anu["id"], "notes": "Call back",
    })
    assert scheduled.status_code == 201
    task = scheduled.json()

    open_tasks = client.get("/tasks", headers=headers).json()
    assert any(t["id"] == task["id"] for t in open_tasks["items"])
    assert any(t["contact_name"] == "Arun Kumar" for t in open_tasks["items"])

    missing_outcome = client.patch(f"/contacts/{contact['id']}/followups/{task['id']}",
                                   json={"status": "completed"}, headers=headers)
    assert missing_outcome.status_code == 422

    done = client.patch(f"/contacts/{contact['id']}/followups/{task['id']}",
                        json={"status": "completed", "outcome": "done", "notes": "Will order Friday"}, headers=headers)
    assert done.status_code == 200 and done.json()["status"] == "completed"
    log = client.get(f"/contacts/{contact['id']}/activities", headers=headers).json()
    assert any(a["notes"] == "Will order Friday" for a in log)


# --- awareness filter, source slug, partners ------------------------------------------------

def test_list_rows_expose_source_slug_and_partner_work_area(client, login, lead_source_id):
    headers, _ = login("admin")
    _contact(client, headers, lead_source_id)
    _contact(client, headers, lead_source_id, name="Plumber Joy", phone="9100000009", contact_type="partner",
             consumer_profile=None, partner_profile={"profession_type": "plumber", "work_area": "Feroke"})
    rows = {r["name"]: r for r in client.get("/contacts", headers=headers).json()["items"]}
    assert rows["Arun Kumar"]["lead_source"] == {"id": lead_source_id("meta"), "name": "Meta", "slug": "meta"}
    assert rows["Plumber Joy"]["work_area"] == "Feroke"
    assert rows["Plumber Joy"]["profession_type"] == "plumber"


def test_unattributed_filter_finds_contacts_without_a_creator(client, login, lead_source_id):
    from database import SessionLocal
    from models import Contact

    headers, _ = login("admin")
    _contact(client, headers, lead_source_id, name="Has creator", phone="9100000020")
    orphan = _contact(client, headers, lead_source_id, name="Legacy row", phone="9100000021")
    with SessionLocal() as db:
        db.get(Contact, orphan["id"]).created_by = None
        db.commit()

    body = client.get("/contacts", params={"unattributed": True}, headers=headers).json()
    assert [r["name"] for r in body["items"]] == ["Legacy row"]
    assert body["items"][0]["created_by"] is None


def test_create_and_update_report_what_is_still_missing(client, login, lead_source_id):
    headers, _ = login("admin")
    created = _contact(client, headers, lead_source_id, consumer_profile=None)
    assert created["is_verified"] is False
    assert created["missing_fields"] == ["house_stage"]

    updated = client.put(f"/contacts/{created['id']}", headers=headers,
                         json={"consumer_profile": {"house_stage": "ready"}})
    assert updated.status_code == 200
    assert updated.json()["is_verified"] is True
    assert updated.json()["missing_fields"] == []

    again = client.put(f"/contacts/{created['id']}", headers=headers, json={"district": "Kannur"}).json()
    assert again["missing_fields"] == []


# --- name-optional edge cases -------------------------------------------------------------------

def test_create_contact_without_name_succeeds(client, login, lead_source_id):
    headers, _ = login("admin")
    body = {
        "contact_type": "consumer",
        "phone": "9800000001",
        "lead_source_id": lead_source_id("meta"),
    }
    r = client.post("/contacts", json=body, headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["name"] is None


def test_nameless_contact_appears_in_list_without_crash(client, login, lead_source_id):
    headers, _ = login("admin")
    client.post("/contacts", json={
        "contact_type": "consumer", "phone": "9800000002",
        "lead_source_id": lead_source_id("meta"),
    }, headers=headers)
    items = client.get("/contacts", headers=headers).json()["items"]
    nameless = [r for r in items if r["phone"] == "9800000002"]
    assert len(nameless) == 1
    assert nameless[0]["name"] is None


def test_nameless_contact_get_by_id_works(client, login, lead_source_id):
    headers, _ = login("admin")
    created = client.post("/contacts", json={
        "contact_type": "consumer", "phone": "9800000003",
        "lead_source_id": lead_source_id("meta"),
    }, headers=headers).json()
    fetched = client.get(f"/contacts/{created['id']}", headers=headers).json()
    assert fetched["name"] is None
    assert fetched["phone"] == "9800000003"


def test_nameless_contact_with_all_other_fields_is_not_verified(client, login, lead_source_id):
    """Name is now required for verification on all contact types."""
    headers, _ = login("admin")
    created = client.post("/contacts", json={
        "contact_type": "consumer",
        "phone": "9800000004",
        "district": "Calicut",
        "address": "MG Road",
        "lead_source_id": lead_source_id("meta"),
        "consumer_profile": {"house_stage": "ready"},
    }, headers=headers).json()
    assert created["is_verified"] is False
    assert "name" in created["missing_fields"]


def test_nameless_contact_missing_fields_includes_name(client, login, lead_source_id):
    """Name is always in missing_fields when absent."""
    headers, _ = login("admin")
    created = client.post("/contacts", json={
        "contact_type": "consumer",
        "phone": "9800000005",
        "lead_source_id": lead_source_id("meta"),
    }, headers=headers).json()
    assert "name" in created["missing_fields"]


def test_phone_uniqueness_works_for_nameless_contact(client, login, lead_source_id):
    headers, _ = login("admin")
    client.post("/contacts", json={
        "contact_type": "consumer", "phone": "9800000006",
        "lead_source_id": lead_source_id("meta"),
    }, headers=headers)
    duplicate = client.post("/contacts", json={
        "contact_type": "consumer", "phone": "9800000006",
        "lead_source_id": lead_source_id("meta"),
    }, headers=headers)
    assert duplicate.status_code == 409


def test_stage_history_is_newest_first_even_within_one_second(client, login, lead_source_id):
    headers, admin = login("admin")
    contact = _contact(client, headers, lead_source_id)
    for stage in ("first_call", "connected"):
        assert client.post(f"/contacts/{contact['id']}/stages", headers=headers,
                           json={"stage_id": _stage_id(stage), "changed_by": admin["id"]}).status_code == 201
    history = client.get(f"/contacts/{contact['id']}/stages", headers=headers).json()
    assert [row["stage_slug"] for row in history] == ["connected", "first_call", "new_lead"]


def test_followup_channel_is_any_active_activity_type_except_internal_note(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _contact(client, headers, lead_source_id, assigned_to=anu["id"])
    due_at = (datetime.datetime.now() + datetime.timedelta(hours=2)).isoformat()

    def schedule(channel):
        return client.post(f"/contacts/{contact['id']}/followups", headers=headers,
                           json={"channel": channel, "due_at": due_at, "assigned_to": anu["id"]})

    demo = schedule("demo")
    assert demo.status_code == 201
    assert demo.json()["channel"] == "demo"
    # The short names older apps send are stored as before.
    assert schedule("phone_call").json()["channel"] == "call"
    assert schedule("internal_note").status_code == 400
    assert schedule("not_a_real_type").status_code == 422


def test_history_lists_stages_followups_and_owner_changes(client, login, lead_source_id):
    headers, anu = login("anu")
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    contact = _contact(client, headers, lead_source_id, assigned_to=anu["id"])
    scheduled = client.post(f"/contacts/{contact['id']}/followups", headers=headers, json={
        "title": "Send quote", "channel": "call", "due_at": (datetime.datetime.now() + datetime.timedelta(days=1)).isoformat(), "assigned_to": anu["id"],
    })
    assert scheduled.status_code == 201
    moved = client.put(f"/contacts/{contact['id']}", json={"assigned_to": raj["id"]}, headers=admin_headers)
    assert moved.status_code == 200

    history = client.get(f"/contacts/{contact['id']}/history", headers=admin_headers).json()
    assert {"stage", "followup", "assignment"} <= {e["event_type"] for e in history}
    assert any(e["event_type"] == "followup" and e["title"] == "Send quote" for e in history)
    change = [e for e in history if e["event_type"] == "assignment" and e["assigned_to_name"] == raj["name"]]
    assert change and change[0]["assigned_from_name"] == anu["name"]
