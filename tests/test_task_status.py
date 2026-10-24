"""Task status on the Tasks screen: the real status (todo / inprogress) is saved, returned, and filterable.

Regression test: the list used to report every open task as "pending", so choosing "In Progress"
snapped back to "Pending" and dropped out of the In Progress tab.
"""
import datetime


def _consumer(client, headers, sources, assigned_to):
    body = {
        "contact_type": "consumer", "name": "Latha Nair", "phone": "9844444444", "district": "Calicut",
        "address": "House 3, Mavoor Road", "lead_source_id": sources("meta"), "assigned_to": assigned_to,
        "consumer_profile": {"house_stage": "ready"},
    }
    response = client.post("/contacts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _due_in_days(days: int) -> str:
    day = datetime.datetime.now() + datetime.timedelta(days=days)
    return day.replace(hour=11, minute=0, second=0, microsecond=0).isoformat()


def test_in_progress_status_is_kept_and_listed_under_in_progress(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _consumer(client, headers, lead_source_id, anu["id"])
    task = client.post(f"/contacts/{contact['id']}/followups", headers=headers, json={
        "channel": "call", "due_at": _due_in_days(1), "assigned_to": anu["id"], "title": "Call Latha",
    }).json()

    moved = client.patch(f"/contacts/{contact['id']}/followups/{task['id']}", headers=headers, json={"status": "inprogress"})
    assert moved.status_code == 200, moved.text

    listed = client.get("/tasks", headers=headers, params={"status": "all", "assigned_to": anu["id"]}).json()
    item = next(row for row in listed["items"] if row["id"] == task["id"])
    assert item["followup_status"] == "inprogress"

    in_progress = client.get("/tasks", headers=headers, params={"status": "inprogress", "assigned_to": anu["id"]}).json()
    assert [row["id"] for row in in_progress["items"]] == [task["id"]]


def test_new_task_starts_as_todo(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _consumer(client, headers, lead_source_id, anu["id"])
    task = client.post(f"/contacts/{contact['id']}/followups", headers=headers, json={
        "channel": "call", "due_at": _due_in_days(2), "assigned_to": anu["id"],
    }).json()

    listed = client.get("/tasks", headers=headers, params={"status": "all", "assigned_to": anu["id"]}).json()
    item = next(row for row in listed["items"] if row["id"] == task["id"])
    assert item["followup_status"] == "todo"


def test_pending_is_no_longer_a_valid_status_to_save(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _consumer(client, headers, lead_source_id, anu["id"])
    task = client.post(f"/contacts/{contact['id']}/followups", headers=headers, json={
        "channel": "call", "due_at": _due_in_days(3), "assigned_to": anu["id"],
    }).json()

    response = client.patch(f"/contacts/{contact['id']}/followups/{task['id']}", headers=headers, json={"status": "pending"})
    assert response.status_code == 422
