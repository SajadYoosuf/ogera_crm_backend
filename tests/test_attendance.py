"""Attendance check-in/check-out and the employee's own last location.

Covers the rules the mobile Home card depends on:
  - one field session per employee per IST day (a second check-in is 409, even after checkout)
  - checkout needs an open session
  - GET /staff/location/me returns only the caller's own position (no IDOR)
"""


def test_today_is_null_before_first_checkin(client, login):
    headers, _ = login("anu")
    response = client.get("/attendance/today", headers=headers)
    assert response.status_code == 200
    assert response.json() is None


def test_checkin_creates_an_open_session_with_gps(client, login):
    headers, _ = login("anu")
    response = client.post(
        "/attendance/checkin", json={"latitude": 11.25, "longitude": 75.78}, headers=headers
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["is_open"] is True
    assert body["checkin_lat"] == 11.25
    assert body["checkin_lng"] == 75.78


def test_second_checkin_same_day_is_409(client, login):
    headers, _ = login("anu")
    assert client.post("/attendance/checkin", json={}, headers=headers).status_code == 201
    response = client.post("/attendance/checkin", json={}, headers=headers)
    assert response.status_code == 409
    assert response.json()["detail"] == "Already checked in today"


def test_checkin_after_checkout_is_still_409(client, login):
    # One session per day: once checked out, the day is over for check-in too.
    headers, _ = login("anu")
    client.post("/attendance/checkin", json={}, headers=headers)
    assert client.post("/attendance/checkout", headers=headers).status_code == 200
    response = client.post("/attendance/checkin", json={}, headers=headers)
    assert response.status_code == 409


def test_today_reports_closed_session_after_checkout(client, login):
    headers, _ = login("anu")
    client.post("/attendance/checkin", json={}, headers=headers)
    client.post("/attendance/checkout", headers=headers)
    body = client.get("/attendance/today", headers=headers).json()
    assert body["is_open"] is False
    assert body["checkout_at"] is not None


def test_checkout_without_checkin_is_404(client, login):
    headers, _ = login("raj")
    assert client.post("/attendance/checkout", headers=headers).status_code == 404


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
