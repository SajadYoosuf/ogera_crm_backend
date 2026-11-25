from tests.test_task_notifications import _lead, _task, _setup  # noqa: F401


def test_reassign_is_saved_and_read_back(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    raj_h, raj = login("raj")
    lead = _lead(client, admin_h, anu["id"])
    task = _task(client, admin_h, lead["id"], anu["id"])
    r = client.patch(f"/leads/{lead['id']}/interactions/{task['id']}/reassign", headers=admin_h,
                     json={"assigned_to": raj["id"]})
    assert r.status_code == 200 and r.json()["assigned_to"] == raj["id"]
    listed = client.get(f"/leads/{lead['id']}/interactions", headers=admin_h, params={"kind": "task"}).json()
    assert [t["assigned_to"] for t in listed] == [raj["id"]]
    tasks = client.get("/lead-tasks", headers=raj_h, params={"bucket": "upcoming"}).json()["items"]
    assert [t["id"] for t in tasks if t["id"] == task["id"]] == [task["id"]]
