"""Employee CRUD: happy path, list filtering, soft-delete behaviour, directory, and self-modification guards.

Duplicate-email/phone rules live in test_employee_duplicates.py.
Password length and change-notification rules live in test_employee_passwords.py.
Authorization (which roles can manage employees) is also verified in test_employee_duplicates.py.

Fixtures
--------
- client  : FastAPI TestClient with a fresh in-memory DB seeded by conftest.
- login   : login(key) → (headers, employee_json). Keys: "admin", "manager", "anu", "raj", "sam".
- no_mail : monkeypatches the mailer so create/update tests don't need email configured.
"""

import pytest
from seed_data import MOBILE_APP


# ---------------------------------------------------------------------------
# Fixture: stub the mailer for tests that trigger it
# ---------------------------------------------------------------------------

@pytest.fixture()
def no_mail(monkeypatch):
    from routes import employees as emp_routes
    monkeypatch.setattr(emp_routes, "send_employee_credentials",
                        lambda *a, **k: {"sent": True, "message": ""})
    monkeypatch.setattr(emp_routes, "send_password_changed_notice",
                        lambda *a, **k: {"sent": True, "message": ""})


# ===========================================================================
# GET /employees — list with filters and pagination
# ===========================================================================

def test_list_returns_shape_items_total_skip_limit(client, login):
    headers, _ = login("admin")
    resp = client.get("/employees", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    for key in ("items", "total", "skip", "limit"):
        assert key in body, f"missing key: {key}"
    assert body["total"] >= 5  # seed seeds 5 employees


def test_list_is_open_to_any_signed_in_employee(client, login):
    """List is not restricted to admin/manager — any authenticated role may read it."""
    # sales_executive must use the mobile-app header to sign in
    headers, _ = login("anu")
    resp = client.get("/employees", headers={**headers, **MOBILE_APP})
    assert resp.status_code == 200


def test_list_requires_authentication(client):
    assert client.get("/employees").status_code == 401


def test_list_filters_by_role(client, login):
    headers, _ = login("admin")
    resp = client.get("/employees", headers=headers, params={"role": "sales_executive"})
    assert resp.status_code == 200
    for emp in resp.json()["items"]:
        assert emp["role"] == "sales_executive"


def test_list_filters_by_is_active_false(client, login):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    client.put(f"/employees/{raj['id']}", json={"is_active": False}, headers=admin_headers)

    resp = client.get("/employees", headers=admin_headers, params={"is_active": "false"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1
    for emp in items:
        assert emp["is_active"] is False


def test_list_filters_by_is_active_true(client, login):
    """After deactivating one employee, active-only list must not include them."""
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    client.put(f"/employees/{raj['id']}", json={"is_active": False}, headers=admin_headers)

    resp = client.get("/employees", headers=admin_headers, params={"is_active": "true"})
    assert resp.status_code == 200
    ids = {emp["id"] for emp in resp.json()["items"]}
    assert raj["id"] not in ids


def test_list_searches_by_name(client, login):
    headers, _ = login("admin")
    resp = client.get("/employees", headers=headers, params={"search": "Anu"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1
    assert all("anu" in emp["name"].lower() or "anu" in emp["email"] for emp in items)


def test_list_searches_by_email(client, login):
    headers, _ = login("admin")
    resp = client.get("/employees", headers=headers, params={"search": "raj@example"})
    assert resp.status_code == 200
    assert len(resp.json()["items"]) >= 1


def test_list_searches_by_phone(client, login):
    headers, _ = login("admin")
    resp = client.get("/employees", headers=headers, params={"search": "9000000003"})
    assert resp.status_code == 200
    assert len(resp.json()["items"]) >= 1


def test_list_pagination_skip_and_limit_do_not_overlap(client, login):
    headers, _ = login("admin")
    page1 = client.get("/employees", headers=headers, params={"skip": 0, "limit": 2}).json()
    page2 = client.get("/employees", headers=headers, params={"skip": 2, "limit": 2}).json()
    ids1 = {emp["id"] for emp in page1["items"]}
    ids2 = {emp["id"] for emp in page2["items"]}
    assert ids1.isdisjoint(ids2), "page 1 and page 2 must not share any employee ids"


def test_list_excludes_soft_deleted_employee(client, login):
    admin_headers, _ = login("admin")
    _, raj = login("raj")

    before = {e["id"] for e in client.get("/employees", headers=admin_headers, params={"limit": 100}).json()["items"]}
    assert raj["id"] in before

    client.delete(f"/employees/{raj['id']}", headers=admin_headers)

    after = {e["id"] for e in client.get("/employees", headers=admin_headers, params={"limit": 100}).json()["items"]}
    assert raj["id"] not in after


def test_list_returns_200_with_empty_items_when_search_matches_nothing(client, login):
    headers, _ = login("admin")
    resp = client.get("/employees", headers=headers, params={"search": "zzz_no_such_person_xyz"})
    assert resp.status_code == 200
    assert resp.json()["items"] == []
    assert resp.json()["total"] == 0


# ===========================================================================
# GET /employees/{id}
# ===========================================================================

def test_get_employee_by_id_returns_correct_record(client, login):
    headers, emp = login("admin")
    resp = client.get(f"/employees/{emp['id']}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == emp["id"]
    assert body["email"] == emp["email"]
    assert "password_hash" not in body  # never exposed


def test_get_employee_requires_authentication(client, login):
    _, emp = login("admin")
    assert client.get(f"/employees/{emp['id']}").status_code == 401


def test_get_employee_returns_404_for_unknown_id(client, login):
    headers, _ = login("admin")
    assert client.get("/employees/999999", headers=headers).status_code == 404


def test_get_employee_returns_404_after_soft_delete(client, login):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    client.delete(f"/employees/{raj['id']}", headers=admin_headers)

    assert client.get(f"/employees/{raj['id']}", headers=admin_headers).status_code == 404


# ===========================================================================
# GET /employees/directory
# ===========================================================================

def test_directory_returns_list_with_id_name_role(client, login):
    headers, _ = login("admin")
    resp = client.get("/employees/directory", headers=headers)
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list) and len(items) >= 1
    for item in items:
        assert {"id", "name", "role"}.issubset(item.keys())


def test_directory_is_open_to_sales_executive(client, login):
    """Any signed-in employee may read the directory, not just admin/manager."""
    headers, _ = login("anu")
    resp = client.get("/employees/directory", headers={**headers, **MOBILE_APP})
    assert resp.status_code == 200


def test_directory_excludes_deactivated_employee(client, login):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    client.put(f"/employees/{raj['id']}", json={"is_active": False}, headers=admin_headers)

    ids = {item["id"] for item in client.get("/employees/directory", headers=admin_headers).json()}
    assert raj["id"] not in ids


def test_directory_excludes_soft_deleted_employee(client, login):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    client.delete(f"/employees/{raj['id']}", headers=admin_headers)

    ids = {item["id"] for item in client.get("/employees/directory", headers=admin_headers).json()}
    assert raj["id"] not in ids


# ===========================================================================
# POST /employees — create
# ===========================================================================

def test_admin_creates_employee_and_response_has_expected_shape(client, login, no_mail):
    headers, _ = login("admin")
    resp = client.post("/employees", json={
        "name": "Jane Doe", "email": "jane.doe@example.com",
        "password": "Passw0rd!", "role": "sales_executive", "phone": "9800000099",
    }, headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "jane.doe@example.com"
    assert body["is_active"] is True
    assert "id" in body and "created_at" in body
    # Create returns an extra field for the mailer outcome
    assert "credentials_email" in body


def test_manager_can_create_employee(client, login, no_mail):
    headers, _ = login("manager")
    resp = client.post("/employees", json={
        "name": "John Doe", "email": "john.doe@example.com",
        "password": "Passw0rd!", "role": "sales_executive", "phone": "9800000098",
    }, headers=headers)
    assert resp.status_code == 201, resp.text


def test_employee_is_not_created_when_credentials_email_fails(client, login, monkeypatch):
    from routes import employees as emp_routes

    monkeypatch.setattr(
        emp_routes,
        "send_employee_credentials",
        lambda *a, **k: {"sent": False, "message": "Credentials email not sent"},
    )
    headers, _ = login("admin")
    payload = {
        "name": "Retry Employee", "email": "retry.employee@example.com",
        "password": "Passw0rd!", "role": "sales_executive",
    }

    failed = client.post("/employees", json=payload, headers=headers)
    assert failed.status_code == 503

    # The failed attempt must not reserve the email address for a retry.
    monkeypatch.setattr(
        emp_routes,
        "send_employee_credentials",
        lambda *a, **k: {"sent": True, "message": "Credentials email sent"},
    )
    retried = client.post("/employees", json=payload, headers=headers)
    assert retried.status_code == 201, retried.text


def test_create_without_phone_is_allowed_and_phone_is_null(client, login, no_mail):
    headers, _ = login("admin")
    resp = client.post("/employees", json={
        "name": "No Phone", "email": "nophone@example.com",
        "password": "Passw0rd!", "role": "sales_executive",
    }, headers=headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["phone"] is None


def test_create_rejects_unknown_role(client, login):
    headers, _ = login("admin")
    resp = client.post("/employees", json={
        "name": "Bad Role", "email": "badrole@example.com",
        "password": "Passw0rd!", "role": "ghost_rider",
    }, headers=headers)
    assert resp.status_code == 422


def test_create_requires_authentication(client):
    resp = client.post("/employees", json={
        "name": "X", "email": "x@x.com", "password": "Passw0rd!", "role": "sales_executive",
    })
    assert resp.status_code == 401


# ===========================================================================
# PUT /employees/{id} — update
# ===========================================================================

def test_admin_updates_employee_name(client, login):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    resp = client.put(f"/employees/{raj['id']}", json={"name": "Raj Updated"}, headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Raj Updated"


def test_update_is_partial_untouched_fields_stay_unchanged(client, login):
    """Sending only `name` must not change email or role."""
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    original_email = raj["email"]
    original_role = raj["role"]
    resp = client.put(f"/employees/{raj['id']}", json={"name": "Partial Update"}, headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == original_email
    assert body["role"] == original_role


def test_can_deactivate_then_reactivate_another_employee(client, login):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    client.put(f"/employees/{raj['id']}", json={"is_active": False}, headers=admin_headers)
    resp = client.put(f"/employees/{raj['id']}", json={"is_active": True}, headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True


def test_update_returns_404_for_soft_deleted_employee(client, login):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    client.delete(f"/employees/{raj['id']}", headers=admin_headers)
    assert client.put(f"/employees/{raj['id']}", json={"name": "Ghost"}, headers=admin_headers).status_code == 404


def test_cannot_change_own_role(client, login):
    admin_headers, admin = login("admin")
    resp = client.put(f"/employees/{admin['id']}", json={"role": "manager"}, headers=admin_headers)
    assert resp.status_code == 400, resp.text
    assert "role" in resp.json()["detail"].lower() or "own" in resp.json()["detail"].lower()


def test_cannot_deactivate_own_account(client, login):
    admin_headers, admin = login("admin")
    resp = client.put(f"/employees/{admin['id']}", json={"is_active": False}, headers=admin_headers)
    assert resp.status_code == 400, resp.text


def test_can_update_own_name_and_email(client, login):
    """Admins may change their own name and email — only role and active-status are blocked."""
    admin_headers, admin = login("admin")
    resp = client.put(f"/employees/{admin['id']}", json={"name": "Ada Updated"}, headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Ada Updated"


def test_update_requires_authentication(client, login):
    _, raj = login("raj")
    assert client.put(f"/employees/{raj['id']}", json={"name": "X"}).status_code == 401


# ===========================================================================
# DELETE /employees/{id} — soft delete
# ===========================================================================

def test_admin_soft_deletes_employee_returns_204(client, login):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    assert client.delete(f"/employees/{raj['id']}", headers=admin_headers).status_code == 204


def test_deleted_employee_is_not_found_by_id(client, login):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    client.delete(f"/employees/{raj['id']}", headers=admin_headers)
    assert client.get(f"/employees/{raj['id']}", headers=admin_headers).status_code == 404


def test_deleted_employee_is_removed_from_list(client, login):
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    client.delete(f"/employees/{raj['id']}", headers=admin_headers)
    ids = {e["id"] for e in client.get("/employees", headers=admin_headers, params={"limit": 100}).json()["items"]}
    assert raj["id"] not in ids


def test_cannot_delete_own_account(client, login):
    admin_headers, admin = login("admin")
    resp = client.delete(f"/employees/{admin['id']}", headers=admin_headers)
    assert resp.status_code == 400


def test_delete_requires_authentication(client, login):
    _, raj = login("raj")
    assert client.delete(f"/employees/{raj['id']}").status_code == 401


def test_soft_delete_keeps_email_reserved_for_new_accounts(client, login, no_mail):
    """Soft-deleted rows still hold the unique email; creating a new account with it must be refused."""
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    raj_email = raj["email"]
    client.delete(f"/employees/{raj['id']}", headers=admin_headers)

    resp = client.post("/employees", json={
        "name": "Reuse Email", "email": raj_email,
        "password": "Passw0rd!", "role": "sales_executive", "phone": "9800000050",
    }, headers=admin_headers)
    assert resp.status_code == 409


def test_soft_delete_frees_phone_number_for_reuse(client, login, no_mail):
    """Unlike email, a deleted employee's phone is not reserved and may be assigned to a new account."""
    admin_headers, _ = login("admin")
    _, raj = login("raj")
    raj_phone = "9000000004"  # seed phone for raj (see seed_data.py STAFF)
    client.delete(f"/employees/{raj['id']}", headers=admin_headers)

    resp = client.post("/employees", json={
        "name": "Reuse Phone", "email": "reuse.phone@example.com",
        "password": "Passw0rd!", "role": "sales_executive", "phone": raj_phone,
    }, headers=admin_headers)
    assert resp.status_code == 201, resp.text
