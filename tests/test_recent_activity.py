"""GET /contacts/recent-activity: who sees which created and assigned events.

- admin and manager see every event.
- other employees see only contacts they created or that were assigned to them.
- deleted contacts never appear.
"""

_PHONES = iter(f"97{n:08d}" for n in range(10_000_000, 99_999_999))


def _create_contact(client, headers, lead_source_id, name, assigned_to=None):
    body = {
        "contact_type": "consumer", "name": name, "phone": next(_PHONES), "district": "Calicut",
        "address": "House 1", "lead_source_id": lead_source_id("meta"),
        "consumer_profile": {"house_stage": "ready"},
    }
    if assigned_to is not None:
        body["assigned_to"] = assigned_to
    response = client.post("/contacts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _recent(client, headers, **params):
    response = client.get("/contacts/recent-activity", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _names(events, event_type):
    return {e["contact_name"] for e in events if e["event_type"] == event_type}


def test_staff_sees_own_created_and_assigned_contacts_only(client, login, lead_source_id):
    admin_headers, _ = login("admin")
    anu_headers, anu = login("anu")
    raj_headers, raj = login("raj")
    _create_contact(client, anu_headers, lead_source_id, "Anu Created", assigned_to=anu["id"])
    _create_contact(client, admin_headers, lead_source_id, "Admin To Raj", assigned_to=raj["id"])
    _create_contact(client, admin_headers, lead_source_id, "Admin Unassigned")

    anu_events = _recent(client, anu_headers)
    raj_events = _recent(client, raj_headers)

    assert "Anu Created" in _names(anu_events, "created")
    assert "Anu Created" in _names(anu_events, "assigned")
    assert "Admin To Raj" not in _names(anu_events, "created")
    assert "Admin Unassigned" not in _names(anu_events, "created")
    assert "Admin To Raj" in _names(raj_events, "assigned")
    assert "Admin To Raj" not in _names(raj_events, "created")
    # Unassigned contacts write no "assigned" event, so nobody sees one for them.
    assert "Admin Unassigned" not in _names(raj_events, "assigned")


def test_admin_sees_every_event(client, login, lead_source_id):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    _create_contact(client, admin_headers, lead_source_id, "Seen By Admin", assigned_to=raj["id"])

    events = _recent(client, admin_headers, limit=50)

    assert "Seen By Admin" in _names(events, "created")
    assert "Seen By Admin" in _names(events, "assigned")
    assigned = next(e for e in events if e["contact_name"] == "Seen By Admin" and e["event_type"] == "assigned")
    assert assigned["assigned_to_name"] == raj["name"]
    assert assigned["actor_name"]


def test_events_are_newest_first_and_limit_is_respected(client, login, lead_source_id):
    admin_headers, _ = login("admin")
    for n in range(3):
        _create_contact(client, admin_headers, lead_source_id, f"Bulk {n}")

    events = _recent(client, admin_headers, limit=2)

    assert len(events) == 2
    timestamps = [e["timestamp"] for e in events]
    assert timestamps == sorted(timestamps, reverse=True)


def test_deleted_contacts_are_hidden(client, login, lead_source_id):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    contact = _create_contact(client, admin_headers, lead_source_id, "Gone Soon", assigned_to=raj["id"])
    assert "Gone Soon" in _names(_recent(client, admin_headers, limit=50), "created")

    response = client.delete(f"/contacts/{contact['id']}", headers=admin_headers)
    assert response.status_code == 204, response.text

    assert "Gone Soon" not in _names(_recent(client, admin_headers, limit=50), "created")
    assert "Gone Soon" not in _names(_recent(client, admin_headers, limit=50), "assigned")


def test_limit_must_be_between_1_and_50(client, login):
    headers, _ = login("admin")

    assert client.get("/contacts/recent-activity", headers=headers, params={"limit": 0}).status_code == 422
    assert client.get("/contacts/recent-activity", headers=headers, params={"limit": 51}).status_code == 422


def test_requires_sign_in(client):
    response = client.get("/contacts/recent-activity")

    assert response.status_code in (401, 403)
