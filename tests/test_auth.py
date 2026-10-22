"""/auth/login: wrong password, unknown email, and deactivated accounts.

Added because no dedicated test existed for the plain login endpoint itself (qa_checklist.yaml,
auth section) — test_mobile_api.py only covers the mobile-facing login path. The real behaviour,
read from routes/auth.py:
  - unknown email and wrong password both return the SAME generic 401 message (no existence leak)
  - a deactivated/deleted account's credentials are checked FIRST, then 403 "Employee account is
    inactive" — so a wrong password on a deactivated account still reports as a wrong password,
    not as "deactivated". That's correct: it doesn't tell an attacker the account exists before
    they've proven they know the password.
  - empty email/password is NOT rejected by the backend (LoginRequest has no min_length) — it falls
    through to the normal "unknown email" 401. The checklist's "blocked client-side with a message"
    is therefore a web-only requirement; nothing to assert here.
"""
from seed_data import email_of, MOBILE_APP


def test_wrong_password_and_unknown_email_give_the_identical_generic_message(client):
    unknown = client.post("/auth/login", json={"email": "nobody@example.com", "password": "whatever123"}, headers=MOBILE_APP)
    wrong = client.post("/auth/login", json={"email": email_of("anu"), "password": "WrongPass9!"}, headers=MOBILE_APP)

    assert unknown.status_code == 401, unknown.text
    assert wrong.status_code == 401, wrong.text
    assert unknown.json()["detail"] == wrong.json()["detail"], \
        "unknown-email and wrong-password must not be distinguishable (security leak otherwise)"


def test_correct_login_returns_a_token_and_the_employee(client):
    response = client.post("/auth/login", json={"email": email_of("admin"), "password": "Passw0rd!"}, headers=MOBILE_APP)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access_token"]
    assert body["employee"]["email"] == email_of("admin")


def test_deactivated_employee_cannot_log_in(client, login):
    admin_headers, _ = login("admin")
    target = client.get("/employees", headers=admin_headers, params={"search": "raj@"}).json()["items"][0]["id"]
    deactivated = client.put(f"/employees/{target}", headers=admin_headers, json={"is_active": False})
    assert deactivated.status_code == 200, deactivated.text

    response = client.post("/auth/login", json={"email": email_of("raj"), "password": "Passw0rd!"}, headers=MOBILE_APP)
    assert response.status_code == 403, response.text
    assert "inactive" in response.json()["detail"].lower()


def test_a_deactivated_accounts_wrong_password_still_reports_as_wrong_password_not_as_deactivated(client, login):
    """Credentials are checked before the active-status check, so a guesser learns nothing about
    whether an account is deactivated until they already know its password."""
    admin_headers, _ = login("admin")
    target = client.get("/employees", headers=admin_headers, params={"search": "raj@"}).json()["items"][0]["id"]
    client.put(f"/employees/{target}", headers=admin_headers, json={"is_active": False})

    response = client.post("/auth/login", json={"email": email_of("raj"), "password": "WrongPass9!"}, headers=MOBILE_APP)
    assert response.status_code == 401, response.text
    assert "inactive" not in response.json()["detail"].lower()
