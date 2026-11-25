from tests.test_task_notifications import _lead, _task, _setup  # noqa: F401


def test_completing_a_follow_up_writes_an_activity_with_done_via_and_note(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    lead = _lead(client, admin_h, anu["id"])
    task = _task(client, anu_h, lead["id"], anu["id"])
    r = client.patch(f"/interactions/{task['id']}", headers=anu_h, json={
        "status": "completed", "outcome": "no_answer", "notes": "no reply", "actual_activity_type": "whatsapp",
    })
    assert r.status_code == 200, r.text
    logs = client.get(f"/leads/{lead['id']}/interactions", headers=anu_h, params={"kind": "log"}).json()
    assert len(logs) == 1
    assert logs[0]["activity_type"] == "whatsapp" and logs[0]["notes"] == "no reply"
    assert logs[0]["outcome"] == "no_answer" and logs[0]["done_by"] == anu["id"]
    assert logs[0]["parent_task_id"] == task["id"]


def test_completing_twice_writes_one_activity(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    lead = _lead(client, admin_h, anu["id"])
    task = _task(client, anu_h, lead["id"], anu["id"])
    body = {"status": "completed", "outcome": "done", "notes": "called"}
    assert client.patch(f"/interactions/{task['id']}", headers=anu_h, json=body).status_code == 200
    client.patch(f"/interactions/{task['id']}", headers=anu_h, json=body)
    logs = client.get(f"/leads/{lead['id']}/interactions", headers=anu_h, params={"kind": "log"}).json()
    assert len(logs) == 1
