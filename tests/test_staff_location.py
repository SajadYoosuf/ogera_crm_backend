"""The employee's own last location.

GET /staff/location/me returns only the caller's own position (no IDOR).
"""


def test_my_location_is_null_before_any_ping(client, login):
    headers, _ = login("sam")
    response = client.get("/staff/location/me", headers=headers)
    assert response.status_code == 200
    assert response.json() is None


def test_my_location_returns_my_last_ping(client, login):
    headers, _ = login("anu")
    client.post("/staff/location", json={"latitude": 11.2588, "longitude": 75.7804}, headers=headers)
    body = client.get("/staff/location/me", headers=headers).json()
    assert body["latitude"] == 11.2588
    assert body["longitude"] == 75.7804
    assert body["recorded_at"]


def test_my_location_never_shows_another_employees_ping(client, login):
    anu_headers, _ = login("anu")
    raj_headers, _ = login("raj")
    client.post("/staff/location", json={"latitude": 10.0, "longitude": 76.0}, headers=anu_headers)
    response = client.get("/staff/location/me", headers=raj_headers)
    assert response.status_code == 200
    assert response.json() is None


def test_my_location_requires_login(client):
    assert client.get("/staff/location/me").status_code in (401, 403)
