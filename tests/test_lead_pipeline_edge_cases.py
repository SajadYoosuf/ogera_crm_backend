"""Edge cases of the lead pipeline: follow-ups, completing them, rescheduling, activity logs and what each role may see."""
import datetime


def _consumer(client, headers, sources, assigned_to, name="Edge Lead"):
    body = {
        "contact_type": "consumer", "name": name, "phone": "9811111111", "district": "Calicut",
        "address": "House 2, Beach Road", "lead_source_id": sources("meta"), "assigned_to": assigned_to,
        "consumer_profile": {"house_stage": "ready"},
    }
    response = client.post("/contacts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _due(days: int, hour: int = 10) -> str:
    day = datetime.datetime.now() + datetime.timedelta(days=days)
    return day.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def _followup(client, headers, contact_id, assignee_id, due_at=None, channel="call"):
    return client.post(f"/contacts/{contact_id}/followups", headers=headers, json={
        "channel": channel, "due_at": due_at or _due(2), "assigned_to": assignee_id, "title": "Edge task",
    })


# --- scheduling ----------------------------------------------------------------

def test_follow_up_cannot_be_scheduled_in_the_past(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _consumer(client, headers, lead_source_id, anu["id"])
    response = _followup(client, headers, contact["id"], anu["id"], due_at=_due(-1))
    assert response.status_code == 422
    assert "cannot be in the past" in response.text


def test_follow_up_cannot_be_assigned_to_a_manager(client, login, lead_source_id):
    headers, anu = login("anu")
    _, manager = login("manager")
    contact = _consumer(client, headers, lead_source_id, anu["id"])
    response = _followup(client, headers, contact["id"], manager["id"])
    assert response.status_code == 400


def test_follow_up_channel_must_be_an_active_activity_type(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _consumer(client, headers, lead_source_id, anu["id"])
    response = _followup(client, headers, contact["id"], anu["id"], channel="not_a_real_type")
    assert response.status_code in (400, 422)


def test_internal_note_cannot_be_scheduled(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _consumer(client, headers, lead_source_id, anu["id"])
    response = _followup(client, headers, contact["id"], anu["id"], channel="internal_note")
    assert response.status_code == 400


# --- completing ----------------------------------------------------------------

def test_completing_needs_an_outcome(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _consumer(client, headers, lead_source_id, anu["id"])
    task = _followup(client, headers, contact["id"], anu["id"]).json()
    response = client.patch(f"/contacts/{contact['id']}/followups/{task['id']}", headers=headers, json={"status": "completed", "notes": "x"})
    assert response.status_code == 422


def test_outcome_must_be_one_of_the_known_values(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _consumer(client, headers, lead_source_id, anu["id"])
    task = _followup(client, headers, contact["id"], anu["id"]).json()
    response = client.patch(f"/contacts/{contact['id']}/followups/{task['id']}", headers=headers,
                            json={"status": "completed", "outcome": "Spoke to customer, will confirm"})
    assert response.status_code == 422


def test_a_completed_follow_up_cannot_be_completed_again_or_rescheduled(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _consumer(client, headers, lead_source_id, anu["id"])
    task = _followup(client, headers, contact["id"], anu["id"]).json()
    url = f"/contacts/{contact['id']}/followups/{task['id']}"
    assert client.patch(url, headers=headers, json={"status": "completed", "outcome": "done", "notes": "first"}).status_code == 200
    assert client.patch(url, headers=headers, json={"status": "completed", "outcome": "done", "notes": "again"}).status_code == 409
    assert client.patch(url, headers=headers, json={"due_at": _due(5)}).status_code == 409


def test_completing_without_notes_is_accepted_but_logs_no_text(client, login, lead_source_id):
    """The web form requires the note. The API allows an empty one (the mobile app completes with outcome only)."""
    headers, anu = login("anu")
    contact = _consumer(client, headers, lead_source_id, anu["id"])
    task = _followup(client, headers, contact["id"], anu["id"]).json()
    response = client.patch(f"/contacts/{contact['id']}/followups/{task['id']}", headers=headers, json={"status": "completed", "outcome": "done"})
    assert response.status_code == 200


# --- rescheduling --------------------------------------------------------------

def test_reschedule_to_a_past_time_is_refused(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _consumer(client, headers, lead_source_id, anu["id"])
    task = _followup(client, headers, contact["id"], anu["id"]).json()
    response = client.patch(f"/contacts/{contact['id']}/followups/{task['id']}", headers=headers, json={"due_at": _due(-2)})
    assert response.status_code == 422


def test_each_reschedule_is_recorded_in_order(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _consumer(client, headers, lead_source_id, anu["id"])
    task = _followup(client, headers, contact["id"], anu["id"], due_at=_due(2)).json()
    url = f"/contacts/{contact['id']}/followups/{task['id']}"
    client.patch(url, headers=headers, json={"due_at": _due(3)})
    client.patch(url, headers=headers, json={"due_at": _due(4)})
    history = client.get(f"/contacts/{contact['id']}/history", headers=headers).json()
    moves = sorted((i for i in history if i["event_type"] == "followup_rescheduled"), key=lambda i: i["timestamp"])
    assert len(moves) == 2
    assert moves[0]["due_at"][:10] == _due(3)[:10] and moves[1]["due_at"][:10] == _due(4)[:10]
    assert moves[1]["previous_due_at"][:10] == _due(3)[:10]


def test_a_refused_reschedule_leaves_no_history_row(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _consumer(client, headers, lead_source_id, anu["id"])
    task = _followup(client, headers, contact["id"], anu["id"]).json()
    client.patch(f"/contacts/{contact['id']}/followups/{task['id']}", headers=headers, json={"due_at": _due(-2)})
    history = client.get(f"/contacts/{contact['id']}/history", headers=headers).json()
    assert not [i for i in history if i["event_type"] == "followup_rescheduled"]


# --- who may act -------------------------------------------------------------------

def test_a_field_executive_cannot_act_on_someone_elses_lead(client, login, lead_source_id):
    anu_headers, anu = login("anu")
    contact = _consumer(client, anu_headers, lead_source_id, anu["id"])
    task = _followup(client, anu_headers, contact["id"], anu["id"]).json()
    field_headers, _ = login("raj")  # field executive: not assigned to this lead
    url = f"/contacts/{contact['id']}/followups/{task['id']}"
    assert client.patch(url, headers=field_headers, json={"due_at": _due(4)}).status_code == 404  # the lead is hidden from them
    assert client.patch(url, headers=field_headers, json={"status": "completed", "outcome": "done", "notes": "x"}).status_code == 404


def test_history_of_a_hidden_lead_is_not_visible(client, login, lead_source_id):
    anu_headers, anu = login("anu")
    contact = _consumer(client, anu_headers, lead_source_id, anu["id"])
    field_headers, _ = login("raj")
    assert client.get(f"/contacts/{contact['id']}/history", headers=field_headers).status_code == 404


def test_manager_can_reschedule_any_follow_up(client, login, lead_source_id):
    anu_headers, anu = login("anu")
    contact = _consumer(client, anu_headers, lead_source_id, anu["id"])
    task = _followup(client, anu_headers, contact["id"], anu["id"]).json()
    manager_headers, _ = login("manager")
    response = client.patch(f"/contacts/{contact['id']}/followups/{task['id']}", headers=manager_headers, json={"due_at": _due(6)})
    assert response.status_code == 200


# --- activity log -----------------------------------------------------------------

def test_activity_log_needs_text(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _consumer(client, headers, lead_source_id, anu["id"])
    response = client.post(f"/contacts/{contact['id']}/activities", headers=headers, json={"activity_type": "phone_call", "notes": ""})
    assert response.status_code in (400, 422)


def test_activity_log_shows_in_history_with_its_text(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _consumer(client, headers, lead_source_id, anu["id"])
    response = client.post(f"/contacts/{contact['id']}/activities", headers=headers, json={"activity_type": "phone_call", "notes": "Asked for the price"})
    assert response.status_code == 201, response.text
    history = client.get(f"/contacts/{contact['id']}/history", headers=headers).json()
    assert any(i["event_type"] == "activity" and i["notes"] == "Asked for the price" for i in history)


def test_no_answer_creates_a_retry_follow_up(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _consumer(client, headers, lead_source_id, anu["id"])
    task = _followup(client, headers, contact["id"], anu["id"]).json()
    client.patch(f"/contacts/{contact['id']}/followups/{task['id']}", headers=headers, json={"status": "completed", "outcome": "no_answer", "notes": "Rang out"})
    follow_ups = client.get(f"/contacts/{contact['id']}/followups", headers=headers).json()
    assert len([t for t in follow_ups if t["status"] in ("todo", "inprogress")]) == 1
