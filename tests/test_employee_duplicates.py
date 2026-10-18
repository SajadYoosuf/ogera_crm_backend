"""Duplicate email and phone on employees: create and update, including case and soft-deleted rows."""

PASSWORD = "Passw0rd!"


def _body(**overrides):
    body = {"name": "New Person", "email": "new.person@example.com", "password": PASSWORD,
            "role": "tele_caller", "phone": "9911100001"}
    body.update(overrides)
    return body


def test_create_rejects_duplicate_email(client, login):
    admin_headers, _ = login("admin")
    response = client.post("/employees", json=_body(email="anu@example.com", phone="9911100002"), headers=admin_headers)
    assert response.status_code == 409


def test_create_rejects_duplicate_email_in_different_case(client, login):
    admin_headers, _ = login("admin")
    response = client.post("/employees", json=_body(email="ANU@Example.com", phone="9911100003"), headers=admin_headers)
    assert response.status_code == 409


def test_create_rejects_duplicate_phone(client, login):
    admin_headers, _ = login("admin")
    response = client.post("/employees", json=_body(phone="9000000003"), headers=admin_headers)
    assert response.status_code == 409


def test_create_rejects_phone_of_a_soft_deleted_employee_is_allowed(client, login):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    assert client.delete(f"/employees/{raj['id']}", headers=admin_headers).status_code == 204
    response = client.post("/employees", json=_body(phone="9000000004"), headers=admin_headers)
    assert response.status_code == 201


def test_create_rejects_email_of_a_soft_deleted_employee(client, login):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    assert client.delete(f"/employees/{raj['id']}", headers=admin_headers).status_code == 204
    response = client.post("/employees", json=_body(email="raj@example.com", phone="9911100004"), headers=admin_headers)
    assert response.status_code == 409


def test_update_rejects_another_employees_email(client, login):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    response = client.put(f"/employees/{raj['id']}", json={"email": "anu@example.com"}, headers=admin_headers)
    assert response.status_code == 409


def test_update_rejects_another_employees_phone(client, login):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    response = client.put(f"/employees/{raj['id']}", json={"phone": "9000000003"}, headers=admin_headers)
    assert response.status_code == 409


def test_update_keeping_own_email_and_phone_is_allowed(client, login):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    response = client.put(f"/employees/{raj['id']}",
                          json={"email": "raj@example.com", "phone": "9000000004"}, headers=admin_headers)
    assert response.status_code == 200


def test_create_rejects_same_phone_with_country_code(client, login):
    admin_headers, _ = login("admin")
    response = client.post("/employees", json=_body(phone="+919000000003", email="cc1@example.com"), headers=admin_headers)
    assert response.status_code == 409


def test_update_rejects_same_phone_with_country_code(client, login):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    response = client.put(f"/employees/{raj['id']}", json={"phone": "+91 900 000 0003"}, headers=admin_headers)
    assert response.status_code == 409


# ---- Only admin/manager can manage employees (qa_checklist.yaml: employees section) ----

def test_non_admin_manager_cannot_create_employee(client, login):
    headers, _ = login("anu")
    response = client.post("/employees", headers=headers, json={
        "name": "Nope", "email": "nope@example.com", "password": "Passw0rd!", "role": "sales_executive",
    })
    assert response.status_code == 403, response.text


def test_non_admin_manager_cannot_update_employee(client, login):
    admin_headers, _ = login("admin")
    target = client.get("/employees", headers=admin_headers, params={"search": "raj@"}).json()["items"][0]["id"]
    staff_headers, _ = login("anu")
    response = client.put(f"/employees/{target}", headers=staff_headers, json={"name": "Hacked"})
    assert response.status_code == 403, response.text


def test_non_admin_manager_cannot_delete_employee(client, login):
    admin_headers, _ = login("admin")
    target = client.get("/employees", headers=admin_headers, params={"search": "raj@"}).json()["items"][0]["id"]
    staff_headers, _ = login("anu")
    response = client.delete(f"/employees/{target}", headers=staff_headers)
    assert response.status_code == 403, response.text
