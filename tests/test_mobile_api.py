"""Endpoints the mobile app depends on, exercised through the real app."""
import datetime

TODAY = datetime.date.today()


def _contact(client, headers, sources, **overrides):
    body = {
        "contact_type": "consumer",
        "name": "Arun Kumar",
        "phone": "9876543210",
        "district": "Calicut",
        "address": "House 4, MG Road",
        "lead_source_id": sources("meta"),
        "consumer_profile": {"awareness_mode": 0, "house_stage": "ready"},
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
    response = client.post("/auth/login", json={"email": "anu@example.com", "password": "nope"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_protected_routes_need_a_token(client):
    assert client.get("/contacts").status_code in (401, 403)
    assert client.get("/tasks").status_code in (401, 403)


# --- contacts: list, map, stages ------------------------------------------------------------

def test_list_rows_carry_awareness_and_location(client, login, lead_source_id):
    headers, _ = login("admin")
    _contact(client, headers, lead_source_id)
    _contact(client, headers, lead_source_id, name="Beena", phone="9876500000",
             consumer_profile={"awareness_mode": 1, "house_stage": "ready"})
    rows = {r["name"]: r for r in client.get("/contacts", headers=headers).json()["items"]}
    assert rows["Arun Kumar"]["awareness_mode"] == 0
    assert rows["Beena"]["awareness_mode"] == 1
    assert rows["Arun Kumar"]["latitude"] is None


def test_map_returns_only_contacts_with_a_location(client, login, lead_source_id):
    headers, _ = login("admin")
    _contact(client, headers, lead_source_id)  # no location
    _contact(client, headers, lead_source_id, name="Site Visit", phone="9876511111",
             lead_source_id=lead_source_id("offline"), latitude=11.25, longitude=75.78)
    body = client.get("/contacts/map", headers=headers).json()
    assert body["total"] == 1
    pin = body["items"][0]
    assert pin["name"] == "Site Visit"
    assert pin["latitude"] == 11.25 and pin["longitude"] == 75.78
    assert pin["stage"] == "new_lead"


def test_map_honours_contact_type_filter(client, login, lead_source_id):
    headers, _ = login("admin")
    offline = lead_source_id("offline")
    _contact(client, headers, lead_source_id, name="A", phone="9000011111", lead_source_id=offline,
             latitude=11.0, longitude=75.0)
    _contact(client, headers, lead_source_id, name="P", phone="9000022222", lead_source_id=offline,
             latitude=11.1, longitude=75.1, contact_type="partner", consumer_profile=None,
             partner_profile={"profession_type": "plumber", "work_area": "Calicut"})
    only_partners = client.get("/contacts/map?contact_type=partner", headers=headers).json()
    assert [p["name"] for p in only_partners["items"]] == ["P"]


def test_stage_types_are_ordered_and_skip_legacy_referral(client, login):
    headers, _ = login("anu")
    stages = client.get("/contacts/stage-types", headers=headers).json()
    ids = [s["id"] for s in stages]
    assert ids[0] == "new_lead" and "referral" not in ids
    assert [s["order_index"] for s in stages] == list(range(len(stages)))
    assert {"won", "lost"} <= set(ids)


def test_stage_change_needs_verified_details(client, login, lead_source_id):
    headers, admin = login("admin")
    unverified = _contact(client, headers, lead_source_id, consumer_profile=None)
    blocked = client.post(f"/contacts/{unverified['id']}/stages",
                          json={"stage": "first_call", "changed_by": admin["id"]}, headers=headers)
    assert blocked.status_code == 409
    assert "house_stage" in blocked.json()["detail"]["missing_fields"]

    verified = _contact(client, headers, lead_source_id, name="Ok", phone="9111111111")
    moved = client.post(f"/contacts/{verified['id']}/stages",
                        json={"stage": "first_call", "changed_by": admin["id"]}, headers=headers)
    assert moved.status_code == 201


# --- staff directory ------------------------------------------------------------------------

def test_directory_is_open_to_staff_but_employee_list_is_not(client, login):
    headers, _ = login("anu")
    directory = client.get("/employees/directory", headers=headers)
    assert directory.status_code == 200
    assert {"Ada Admin", "Anu Sales"} <= {e["name"] for e in directory.json()}
    assert set(directory.json()[0]) == {"id", "name", "role", "phone"}
    assert client.get("/employees", headers=headers).status_code == 403


def test_directory_hides_deactivated_staff(client, login):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    client.put(f"/employees/{raj['id']}", json={"is_active": False}, headers=admin_headers)
    names = [e["name"] for e in client.get("/employees/directory", headers=admin_headers).json()]
    assert "Raj Field" not in names


# --- performance ----------------------------------------------------------------------------

def test_performance_team_view_for_managers_self_view_for_staff(client, login, lead_source_id):
    admin_headers, _ = login("admin")
    anu_headers, anu = login("anu")
    _contact(client, anu_headers, lead_source_id, assigned_to=anu["id"])
    _contact(client, anu_headers, lead_source_id, name="B", phone="9222222222",
             consumer_profile={"awareness_mode": 1, "house_stage": "ready"})
    _contact(client, admin_headers, lead_source_id, name="C", phone="9333333333")

    window = {"start_date": TODAY.isoformat(), "end_date": TODAY.isoformat()}
    team = client.get("/performance", params=window, headers=admin_headers).json()
    assert team["scope"] == "team"
    assert team["totals"] == {"leads": 2, "awareness": 1}
    by_name = {s["name"]: s for s in team["staff"]}
    assert by_name["Anu Sales"] == {"id": anu["id"], "name": "Anu Sales", "leads": 1, "awareness": 1}

    own = client.get("/performance", params=window, headers=anu_headers).json()
    assert own["scope"] == "self"
    assert own["totals"] == {"leads": 1, "awareness": 1}
    assert [s["name"] for s in own["staff"]] == ["Anu Sales"]


def test_performance_empty_range_still_lists_the_staff_member(client, login):
    headers, anu = login("anu")
    long_ago = {"start_date": "2020-01-01", "end_date": "2020-01-02"}
    body = client.get("/performance", params=long_ago, headers=headers).json()
    assert body["staff"] == [{"id": anu["id"], "name": "Anu Sales", "leads": 0, "awareness": 0}]


def test_performance_rejects_inverted_range(client, login):
    headers, _ = login("admin")
    response = client.get("/performance", params={"start_date": "2026-02-02", "end_date": "2026-02-01"}, headers=headers)
    assert response.status_code == 400


# --- follow-up tasks ------------------------------------------------------------------------

def test_schedule_then_complete_a_followup(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _contact(client, headers, lead_source_id, assigned_to=anu["id"])
    scheduled = client.post(f"/contacts/{contact['id']}/activities", headers=headers, json={
        "type": "phone_call", "scheduled_date": TODAY.isoformat(), "assigned_to": anu["id"], "notes": "Call back",
    })
    assert scheduled.status_code == 201
    task = scheduled.json()

    open_tasks = client.get("/tasks", headers=headers, params={"include_overdue": True,
                                                                "date_from": TODAY.isoformat(),
                                                                "date_to": TODAY.isoformat()}).json()
    assert [t["id"] for t in open_tasks["items"]] == [task["id"]]
    assert open_tasks["items"][0]["contact_name"] == "Arun Kumar"

    missing_outcome = client.patch(f"/contacts/{contact['id']}/activities/{task['id']}",
                                   json={"status": "done"}, headers=headers)
    assert missing_outcome.status_code == 422

    done = client.patch(f"/contacts/{contact['id']}/activities/{task['id']}",
                        json={"status": "done", "outcome": "Will order Friday"}, headers=headers)
    assert done.status_code == 200 and done.json()["status"] == "done"
    log = client.get(f"/contacts/{contact['id']}/activities", headers=headers).json()
    assert any(a["notes"] == "Will order Friday" and a["status"] == "done" for a in log)


def test_staff_cannot_complete_someone_elses_task(client, login, lead_source_id):
    anu_headers, anu = login("anu")
    raj_headers, _ = login("raj")
    contact = _contact(client, anu_headers, lead_source_id, assigned_to=anu["id"])
    task = client.post(f"/contacts/{contact['id']}/activities", headers=anu_headers, json={
        "type": "phone_call", "scheduled_date": TODAY.isoformat(), "assigned_to": anu["id"]}).json()
    response = client.patch(f"/contacts/{contact['id']}/activities/{task['id']}",
                            json={"status": "done", "outcome": "x"}, headers=raj_headers)
    assert response.status_code == 403


# --- staff locations ------------------------------------------------------------------------

def _point(minutes_ago: int, lat=11.25, lng=75.78, moving=True):
    stamp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    return {"latitude": lat, "longitude": lng, "recordedAt": stamp.isoformat(),
            "isMoving": moving, "accuracy": 8.5, "speed": 1.2, "batteryLevel": 80}


def test_location_upload_dedupes_and_feeds_live_and_route(client, login):
    raj_headers, raj = login("raj")
    admin_headers, _ = login("admin")
    batch = {"points": [_point(5), _point(3, lat=11.26), _point(1, lat=11.27)]}
    assert client.post("/staff/locations", json=batch, headers=raj_headers).json() == {"saved": 3}
    client.post("/staff/locations", json=batch, headers=raj_headers)  # retry after a half-failed flush

    live = client.get("/staff/locations/live", headers=admin_headers).json()["staff"]
    assert [row["userId"] for row in live] == [raj["id"]]
    assert live[0]["state"] == "moving" and live[0]["latitude"] == 11.27

    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).date().isoformat()
    mine = client.get("/staff/locations/me", params={"date": today}, headers=raj_headers).json()
    assert len(mine["points"]) == 3  # the retry created no duplicates
    assert mine["distanceMeters"] > 0

    history = client.get("/staff/locations/history", params={"userId": raj["id"], "date": today},
                         headers=admin_headers)
    assert history.status_code == 200 and len(history.json()["points"]) == 3


def test_live_map_and_history_are_head_only(client, login):
    headers, anu = login("anu")
    assert client.get("/staff/locations/live", headers=headers).status_code == 403
    assert client.get("/staff/locations/history", params={"userId": anu["id"], "date": "2026-01-01"},
                      headers=headers).status_code == 403


def test_reported_location_off_shows_on_the_live_map(client, login):
    raj_headers, _ = login("raj")
    admin_headers, _ = login("admin")
    client.post("/staff/locations", json={"points": [_point(2)]}, headers=raj_headers)
    assert client.post("/staff/locations/state", json={"state": "location_disabled"},
                       headers=raj_headers).status_code == 204
    live = client.get("/staff/locations/live", headers=admin_headers).json()["staff"]
    assert live[0]["state"] == "location_disabled"
    assert client.post("/staff/locations/state", json={"state": "bogus"}, headers=raj_headers).status_code == 422


# --- sales ----------------------------------------------------------------------------------

def test_sales_staff_can_read_but_only_managers_write(client, login):
    admin_headers, _ = login("admin")
    anu_headers, _ = login("anu")
    created = client.post("/sales", headers=admin_headers, json={
        "name": "Buyer One", "phone": "9947270717", "purchase_date": "2026-09-01",
        "purchased_product": "500 LTR Tank"})
    assert created.status_code == 201
    assert client.get("/sales", headers=anu_headers).json()["total"] == 1
    assert client.post("/sales", headers=anu_headers, json={"name": "X"}).status_code == 403


# --- awareness filter, source slug, partners ------------------------------------------------

def test_awareness_filter_splits_leads_from_awareness_contacts(client, login, lead_source_id):
    headers, _ = login("admin")
    _contact(client, headers, lead_source_id, name="Lead one", phone="9100000001")
    _contact(client, headers, lead_source_id, name="Unassessed", phone="9100000002", consumer_profile=None)
    _contact(client, headers, lead_source_id, name="Aware one", phone="9100000003",
             consumer_profile={"awareness_mode": 1, "house_stage": "ready"})

    def names(**params):
        body = client.get("/contacts", params={"contact_type": "consumer", **params}, headers=headers).json()
        return sorted(r["name"] for r in body["items"]), body["total"]

    assert names(awareness_mode=1) == (["Aware one"], 1)
    assert names(awareness_mode=0) == (["Lead one", "Unassessed"], 2)  # not assessed counts as a lead
    assert names()[1] == 3
    assert client.get("/contacts", params={"awareness_mode": 2}, headers=headers).status_code == 422


def test_list_rows_expose_source_slug_and_partner_work_area(client, login, lead_source_id):
    headers, _ = login("admin")
    _contact(client, headers, lead_source_id)
    _contact(client, headers, lead_source_id, name="Plumber Joy", phone="9100000009", contact_type="partner",
             consumer_profile=None, partner_profile={"profession_type": "plumber", "work_area": "Feroke"})
    rows = {r["name"]: r for r in client.get("/contacts", headers=headers).json()["items"]}
    assert rows["Arun Kumar"]["lead_source"] == {"id": lead_source_id("meta"), "name": "Meta", "slug": "meta"}
    assert rows["Plumber Joy"]["work_area"] == "Feroke"
    assert rows["Plumber Joy"]["profession_type"] == "plumber"


def test_map_pins_carry_awareness_mode(client, login, lead_source_id):
    headers, _ = login("admin")
    _contact(client, headers, lead_source_id, name="Aware", phone="9100000010",
             lead_source_id=lead_source_id("offline"), latitude=11.0, longitude=75.0,
             consumer_profile={"awareness_mode": 1, "house_stage": "ready"})
    pins = client.get("/contacts/map", params={"awareness_mode": 1}, headers=headers).json()["items"]
    assert [p["awareness_mode"] for p in pins] == [1]
    assert client.get("/contacts/map", params={"awareness_mode": 0}, headers=headers).json()["total"] == 0


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


def test_stage_history_is_newest_first_even_within_one_second(client, login, lead_source_id):
    headers, admin = login("admin")
    contact = _contact(client, headers, lead_source_id)
    for stage in ("first_call", "connected"):
        assert client.post(f"/contacts/{contact['id']}/stages", headers=headers,
                           json={"stage": stage, "changed_by": admin["id"]}).status_code == 201
    history = client.get(f"/contacts/{contact['id']}/stages", headers=headers).json()
    assert [row["stage"] for row in history] == ["connected", "first_call", "new_lead"]
