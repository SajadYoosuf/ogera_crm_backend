"""Employee passwords: 6 to 15 characters, and an email when an admin changes one. The mailer is stubbed."""
import pytest

from routes import employees as employee_routes

NEW_EMPLOYEE = {
    "name": "Pat Password", "email": "pat@example.com", "role": "tele_caller", "phone": "9999900002",
}


@pytest.fixture()
def notices(monkeypatch):
    sent: list[dict] = []

    def fake_notice(name, email):
        sent.append({"name": name, "email": email})
        return {"sent": True, "message": "Notice sent"}

    monkeypatch.setattr(employee_routes, "send_password_changed_notice", fake_notice)
    monkeypatch.setattr(employee_routes, "send_employee_credentials",
                        lambda *a, **k: {"sent": True, "message": "Credentials sent"})
    return sent


@pytest.mark.parametrize("password", ["abc12", "a1" * 8])  # 5 characters, and 16 characters
def test_create_rejects_passwords_outside_6_to_15_characters(client, login, password):
    admin_headers, _ = login("admin")

    response = client.post("/employees", json={**NEW_EMPLOYEE, "password": password}, headers=admin_headers)

    assert response.status_code == 422


def test_create_accepts_a_15_character_password(client, login, notices):
    admin_headers, _ = login("admin")

    response = client.post("/employees", json={**NEW_EMPLOYEE, "password": "abcdef123456789"}, headers=admin_headers)

    assert response.status_code == 201, response.text


def test_create_accepts_a_6_character_password(client, login, notices):
    admin_headers, _ = login("admin")

    response = client.post("/employees", json={**NEW_EMPLOYEE, "password": "abc123"}, headers=admin_headers)

    assert response.status_code == 201, response.text


def test_admin_changing_a_password_emails_that_employee(client, login, notices):
    admin_headers, _ = login("admin")
    _, raj = login("raj")

    response = client.put(f"/employees/{raj['id']}", json={"password": "NewPass123"}, headers=admin_headers)

    assert response.status_code == 200, response.text
    assert notices == [{"name": "Raj Field", "email": "raj@example.com"}]


def test_editing_other_fields_does_not_send_a_password_email(client, login, notices):
    admin_headers, _ = login("admin")
    _, raj = login("raj")

    response = client.put(f"/employees/{raj['id']}", json={"name": "Raj F"}, headers=admin_headers)

    assert response.status_code == 200, response.text
    assert notices == []


def test_password_update_rejects_a_password_over_15_characters(client, login, notices):
    admin_headers, _ = login("admin")
    _, raj = login("raj")

    response = client.put(f"/employees/{raj['id']}", json={"password": "a1" * 8}, headers=admin_headers)

    assert response.status_code == 422
    assert notices == []
