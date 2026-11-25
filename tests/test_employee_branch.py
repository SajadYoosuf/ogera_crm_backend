"""An employee's branch is saved as branch_id and comes back as its code (e.g. "malappuram")."""


def _admin(login):
    headers, _ = login("admin")
    return headers


def test_branch_is_saved_and_returned_on_list_and_detail(client, login):
    admin = _admin(login)
    _, sam = login("sam")

    changed = client.put(f"/employees/{sam['id']}", headers=admin, json={"branch": "malappuram"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["branch"] == "malappuram"

    detail = client.get(f"/employees/{sam['id']}", headers=admin)
    assert detail.json()["branch"] == "malappuram"

    listed = client.get("/employees?limit=100", headers=admin).json()["items"]
    row = next(item for item in listed if item["id"] == sam["id"])
    assert row["branch"] == "malappuram"

    cleared = client.put(f"/employees/{sam['id']}", headers=admin, json={"branch": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["branch"] is None


def test_unknown_branch_is_refused(client, login):
    admin = _admin(login)
    _, sam = login("sam")

    bad = client.put(f"/employees/{sam['id']}", headers=admin, json={"branch": "kochi"})
    assert bad.status_code == 422, bad.text
