"""Mark done and reschedule from the Tasks screen: the activity is logged, and a reschedule shows in the lead's history."""
import datetime


def _consumer(client, headers, sources, assigned_to):
    body = {
        "contact_type": "consumer", "name": "Meena Devi", "phone": "9833333333", "district": "Calicut",
        "address": "House 9, Beach Road", "lead_source_id": sources("meta"), "assigned_to": assigned_to,
        "consumer_profile": {"house_stage": "ready"},
    }
    response = client.post("/contacts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _at(days: int, hour: int) -> datetime.datetime:
    day = datetime.datetime.now() + datetime.timedelta(days=days)
    return day.replace(hour=hour, minute=30, second=0, microsecond=0)


def test_reschedule_is_recorded_in_history_with_the_old_date(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _consumer(client, headers, lead_source_id, anu["id"])
    old_due = _at(2, 10)
    task = client.post(f"/contacts/{contact['id']}/followups", headers=headers, json={
        "channel": "call", "due_at": old_due.isoformat(), "assigned_to": anu["id"], "title": "Call about quote",
    }).json()

    new_due = _at(5, 16)
    moved = client.patch(f"/contacts/{contact['id']}/followups/{task['id']}", headers=headers, json={"due_at": new_due.isoformat()})
    assert moved.status_code == 200, moved.text
    assert moved.json()["due_at"].startswith(new_due.isoformat()[:16])

    history = client.get(f"/contacts/{contact['id']}/history", headers=headers).json()
    moves = [item for item in history if item["event_type"] == "followup_rescheduled"]
    assert len(moves) == 1
    assert moves[0]["followup_status"] == "rescheduled"
    assert moves[0]["title"] == "Call about quote"
    assert moves[0]["previous_due_at"].startswith(old_due.isoformat()[:16])
    assert moves[0]["due_at"].startswith(new_due.isoformat()[:16])
    assert moves[0]["actor_name"] == anu["name"]


def test_mark_done_logs_the_activity_with_what_happened(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _consumer(client, headers, lead_source_id, anu["id"])
    task = client.post(f"/contacts/{contact['id']}/followups", headers=headers, json={
        "channel": "call", "due_at": _at(1, 11).isoformat(), "assigned_to": anu["id"],
    }).json()

    done = client.patch(f"/contacts/{contact['id']}/followups/{task['id']}", headers=headers,
                        json={"status": "completed", "outcome": "done", "notes": "Spoke to Meena, quote on Friday"})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "completed"

    history = client.get(f"/contacts/{contact['id']}/history", headers=headers).json()
    logged = [item for item in history if item["event_type"] == "activity" and item["followup_id"] == task["id"]]
    assert logged and logged[0]["notes"] == "Spoke to Meena, quote on Friday"
