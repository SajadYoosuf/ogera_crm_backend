"""Owner rule for lead work (services/lead_access.py).

- admin and manager: may log activities, schedule follow-ups and change any follow-up, on any lead.
- other employees: may log and schedule only on a lead assigned to them, and may complete or
  reschedule only follow-ups assigned to them.
- everyone: may still read a lead's activity log, follow-ups and history.
"""
import datetime

FUTURE = (datetime.datetime.now() + datetime.timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)


_PHONES = iter(f"98{n:08d}" for n in range(10_000_000, 99_999_999))


def _lead(client, headers, lead_source_id, owner_id):
    # Each lead needs its own phone number (phones are unique per contact).
    body = {
        "contact_type": "consumer", "name": "Lead Owned", "phone": next(_PHONES), "district": "Calicut",
        "address": "House 1", "lead_source_id": lead_source_id("meta"),
        "consumer_profile": {"house_stage": "ready"}, "assigned_to": owner_id,
    }
    response = client.post("/contacts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _followup(client, headers, contact_id, assigned_to):
    response = client.post(f"/contacts/{contact_id}/followups", headers=headers, json={
        "channel": "call", "due_at": FUTURE.isoformat(), "assigned_to": assigned_to})
    assert response.status_code == 201, response.text
    return response.json()


def _log(client, headers, contact_id):
    return client.post(f"/contacts/{contact_id}/activities", headers=headers,
                       json={"activity_type": "phone_call", "notes": "Spoke about the tank"})


def test_employee_logs_activity_on_own_lead(client, login, lead_source_id):
    headers, anu = login("anu")
    lead = _lead(client, headers, lead_source_id, anu["id"])

    assert _log(client, headers, lead["id"]).status_code == 201


def test_employee_cannot_log_activity_on_someone_elses_lead(client, login, lead_source_id):
    admin_headers, _ = login("admin")
    raj_headers, raj = login("raj")
    anu_headers, _ = login("anu")
    raj_lead = _lead(client, admin_headers, lead_source_id, raj["id"])

    response = _log(client, anu_headers, raj_lead["id"])

    assert response.status_code == 403
    assert "assigned employee" in response.json()["detail"]


def test_employee_cannot_log_on_an_unassigned_lead_made_by_someone_else(client, login, lead_source_id):
    admin_headers, _ = login("admin")
    headers, _ = login("anu")
    lead = _lead(client, admin_headers, lead_source_id, None)

    assert _log(client, headers, lead["id"]).status_code == 403


def test_admin_and_manager_log_on_any_lead(client, login, lead_source_id):
    admin_headers, _ = login("admin")
    manager_headers, _ = login("manager")
    _, anu = login("anu")
    lead = _lead(client, admin_headers, lead_source_id, anu["id"])
    unassigned = _lead(client, admin_headers, lead_source_id, None)

    assert _log(client, admin_headers, lead["id"]).status_code == 201
    assert _log(client, manager_headers, lead["id"]).status_code == 201
    assert _log(client, admin_headers, unassigned["id"]).status_code == 201


def test_employee_schedules_only_on_own_lead_and_for_themselves(client, login, lead_source_id):
    admin_headers, _ = login("admin")
    anu_headers, anu = login("anu")
    _, raj = login("raj")
    own = _lead(client, anu_headers, lead_source_id, anu["id"])
    raj_lead = _lead(client, admin_headers, lead_source_id, raj["id"])

    body = {"channel": "call", "due_at": FUTURE.isoformat(), "assigned_to": anu["id"]}
    assert client.post(f"/contacts/{own['id']}/followups", headers=anu_headers, json=body).status_code == 201
    assert client.post(f"/contacts/{raj_lead['id']}/followups", headers=anu_headers, json=body).status_code == 403

    # On her own lead, but for a colleague: refused.
    to_raj = {**body, "assigned_to": raj["id"]}
    assert client.post(f"/contacts/{own['id']}/followups", headers=anu_headers, json=to_raj).status_code == 403


def test_admin_schedules_for_any_assignee_on_any_lead(client, login, lead_source_id):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    lead = _lead(client, admin_headers, lead_source_id, None)

    response = client.post(f"/contacts/{lead['id']}/followups", headers=admin_headers, json={
        "channel": "call", "due_at": FUTURE.isoformat(), "assigned_to": raj["id"]})
    assert response.status_code == 201


def test_assignee_completes_own_follow_up_and_others_cannot(client, login, lead_source_id):
    admin_headers, _ = login("admin")
    anu_headers, anu = login("anu")
    raj_headers, _ = login("raj")
    lead = _lead(client, admin_headers, lead_source_id, anu["id"])
    anus_task = _followup(client, admin_headers, lead["id"], anu["id"])
    done = {"status": "completed", "outcome": "done", "notes": "Sent the quote"}

    refused = client.patch(f"/contacts/{lead['id']}/followups/{anus_task['id']}", headers=raj_headers, json=done)
    assert refused.status_code == 403
    assert client.patch(f"/contacts/{lead['id']}/followups/{anus_task['id']}", headers=anu_headers, json=done).status_code == 200


def test_assignee_reschedules_own_task_and_others_cannot(client, login, lead_source_id):
    admin_headers, _ = login("admin")
    anu_headers, anu = login("anu")
    raj_headers, _ = login("raj")
    lead = _lead(client, admin_headers, lead_source_id, anu["id"])
    task = _followup(client, admin_headers, lead["id"], anu["id"])
    later = {"due_at": (FUTURE + datetime.timedelta(days=2)).isoformat()}

    assert client.patch(f"/contacts/{lead['id']}/followups/{task['id']}", headers=raj_headers, json=later).status_code == 403
    assert client.patch(f"/contacts/{lead['id']}/followups/{task['id']}", headers=anu_headers, json=later).status_code == 200


def test_admin_can_change_any_follow_up(client, login, lead_source_id):
    admin_headers, _ = login("admin")
    _, anu = login("anu")
    lead = _lead(client, admin_headers, lead_source_id, anu["id"])
    task = _followup(client, admin_headers, lead["id"], anu["id"])

    response = client.patch(f"/contacts/{lead['id']}/followups/{task['id']}", headers=admin_headers,
                            json={"status": "completed", "outcome": "done", "notes": "Handled by head"})
    assert response.status_code == 200


def test_task_route_applies_the_same_assignee_rule(client, login, lead_source_id):
    admin_headers, _ = login("admin")
    anu_headers, anu = login("anu")
    raj_headers, _ = login("raj")
    lead = _lead(client, admin_headers, lead_source_id, anu["id"])
    task = _followup(client, admin_headers, lead["id"], anu["id"])
    done = {"status": "completed", "outcome": "done", "notes": "Done"}

    assert client.patch(f"/tasks/{task['id']}", headers=raj_headers, json=done).status_code == 403
    assert client.patch(f"/tasks/{task['id']}", headers=anu_headers, json=done).status_code == 200


def test_employee_cannot_create_a_standalone_task_for_someone_else(client, login):
    anu_headers, anu = login("anu")
    _, raj = login("raj")
    body = {"title": "Call back", "due_at": FUTURE.isoformat(), "priority": "medium"}

    assert client.post("/tasks", headers=anu_headers, json={**body, "assigned_to": raj["id"]}).status_code == 403
    assert client.post("/tasks", headers=anu_headers, json={**body, "assigned_to": anu["id"]}).status_code == 201


def test_everyone_can_still_read_another_employees_lead(client, login, lead_source_id):
    admin_headers, _ = login("admin")
    raj_headers, raj = login("raj")
    anu_headers, _ = login("anu")
    lead = _lead(client, admin_headers, lead_source_id, raj["id"])
    _followup(client, admin_headers, lead["id"], raj["id"])

    for path in ("activities", "followups", "history"):
        assert client.get(f"/contacts/{lead['id']}/{path}", headers=anu_headers).status_code == 200


def test_employee_logs_activity_on_lead_they_created_but_not_assigned(client, login, lead_source_id):
    headers, _ = login("anu")
    lead = _lead(client, headers, lead_source_id, None)

    assert _log(client, headers, lead["id"]).status_code == 201


def test_employee_cannot_schedule_followup_on_lead_they_only_created(client, login, lead_source_id):
    headers, anu = login("anu")
    lead = _lead(client, headers, lead_source_id, None)

    response = client.post(f"/contacts/{lead['id']}/followups", headers=headers, json={
        "channel": "call", "due_at": FUTURE.isoformat(), "assigned_to": anu["id"]})

    assert response.status_code == 403
