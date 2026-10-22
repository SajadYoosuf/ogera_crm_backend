"""Settings screen: dropdown options, company profile, permission matrix, and my account.

Who may do what (routes/settings.py, routes/auth.py):
- Dropdown options: admin and manager read and change them; everyone signed in reads the active ones.
- Company profile: admin changes it; admin and manager read it.
- Permission matrix: admin and manager read it and switch staff roles' actions. Admin is never switched off.
  Only an admin changes what managers may do.
- My account: anyone signed in edits their own name, email (with the current password) and password.
"""
from seed_data import PASSWORD, email_of, MOBILE_APP


def _admin(login):
    headers, _ = login("admin")
    return headers


def _manager(login):
    headers, _ = login("manager")
    return headers


def _staff(login):
    # Any non-admin/manager role; sales_executive stands in for "everyone else".
    headers, _ = login("anu")
    return headers


# ---- Enums ----------------------------------------------------------------

def test_list_all_enums_is_admin_and_manager_not_staff(client, login):
    assert client.get("/settings/enums", headers=_admin(login)).status_code == 200
    assert client.get("/settings/enums", headers=_manager(login)).status_code == 200
    assert client.get("/settings/enums", headers=_staff(login)).status_code == 403


def test_list_one_enum_category_is_open_to_any_signed_in_employee(client, login):
    for key in ("admin", "manager", "anu", "raj", "sam"):
        headers, _ = login(key)
        response = client.get("/settings/enums/district", headers=headers)
        assert response.status_code == 200, f"{key}: {response.text}"


def test_enum_writes_are_open_to_manager_but_not_staff(client, login):
    admin, manager, staff = _admin(login), _manager(login), _staff(login)

    created = client.post("/settings/enums/district", headers=admin, json={"value": "temp_stage", "label": "Temp"})
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]

    renamed = client.patch(f"/settings/enums/district/{item_id}", headers=manager, json={"label": "Temp 2"})
    assert renamed.status_code == 200, renamed.text

    reordered = client.post("/settings/enums/district/reorder", headers=manager, json={"ordered_ids": [item_id]})
    assert reordered.status_code == 200, reordered.text

    for headers, label in ((staff, "staff"),):
        denied = client.post("/settings/enums/district", headers=headers, json={"value": "x", "label": "X"})
        assert denied.status_code == 403, f"{label} create: {denied.text}"
        denied = client.patch(f"/settings/enums/district/{item_id}", headers=headers, json={"label": "Y"})
        assert denied.status_code == 403, f"{label} update: {denied.text}"
        denied = client.delete(f"/settings/enums/district/{item_id}", headers=headers)
        assert denied.status_code == 403, f"{label} delete: {denied.text}"
        denied = client.post("/settings/enums/district/reorder", headers=headers, json={"ordered_ids": [item_id]})
        assert denied.status_code == 403, f"{label} reorder: {denied.text}"

    assert client.delete(f"/settings/enums/district/{item_id}", headers=manager).status_code == 200


def test_enum_category_must_be_a_known_one(client, login):
    assert client.get("/settings/enums/not_a_real_category", headers=_admin(login)).status_code == 404


def test_lead_stage_can_be_added_to_one_pipeline(client, login):
    admin, manager, staff = _admin(login), _manager(login), _staff(login)

    created = client.post("/settings/enums/lead_stage", headers=manager, json={"label": "Site Visit Booked", "group": "partner"})
    assert created.status_code == 201, created.text
    stage = created.json()
    assert stage["group"] == "partner"

    duplicate = client.post("/settings/enums/lead_stage", headers=manager, json={"label": "Site Visit Booked", "group": "partner"})
    assert duplicate.status_code == 409, duplicate.text

    # Same name in another pipeline is a different stage.
    other = client.post("/settings/enums/lead_stage", headers=manager, json={"label": "Site Visit Booked", "group": "dealer"})
    assert other.status_code == 201, other.text

    listed = client.get("/settings/enums", headers=admin).json()["lead_stage"]
    assert any(row["id"] == stage["id"] and row["group"] == "partner" for row in listed)

    bad_pipeline = client.post("/settings/enums/lead_stage", headers=manager, json={"label": "X", "group": "vendor"})
    assert bad_pipeline.status_code == 422, bad_pipeline.text

    assert client.post("/settings/enums/lead_stage", headers=staff, json={"label": "Y", "group": "partner"}).status_code == 403

    # Tidy up: the stages are unused, so removing them just hides them.
    for row in (stage, other.json()):
        assert client.delete(f"/settings/enums/lead_stage/{row['id']}", headers=admin).status_code == 200


# ---- Company profile --------------------------------------------------------

def test_company_read_is_manager_and_admin_but_not_staff(client, login):
    assert client.get("/settings/company", headers=_admin(login)).status_code == 200
    assert client.get("/settings/company", headers=_manager(login)).status_code == 200
    assert client.get("/settings/company", headers=_staff(login)).status_code == 403


def test_staff_cannot_read_or_write_company(client, login):
    staff = _staff(login)
    assert client.get("/settings/company", headers=staff).status_code == 403
    assert client.patch("/settings/company", headers=staff, json={"company_name": "Hacked"}).status_code == 403


# ---- Permission matrix (Roles tab) -----------------------------------------

def _cell(matrix, feature_key, role_slug):
    feature = next(f for f in matrix["features"] if f["key"] == feature_key)
    return feature["levels"][role_slug]


# ---- My account --------------------------------------------------------------

def test_change_my_name(client, login):
    headers, me = login("sam")
    updated = client.patch("/auth/me", headers=headers, json={"name": "  Sam Renamed  "})
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Sam Renamed"

    client.patch("/auth/me", headers=headers, json={"name": me["name"]})


def test_changing_my_email_needs_the_current_password_and_a_free_address(client, login):
    headers, me = login("sam")
    new_email = "sam.new@example.com"

    no_password = client.patch("/auth/me", headers=headers, json={"email": new_email})
    assert no_password.status_code == 400, no_password.text

    wrong_password = client.patch("/auth/me", headers=headers, json={"email": new_email, "current_password": "nope123"})
    assert wrong_password.status_code == 400, wrong_password.text

    taken = client.patch("/auth/me", headers=headers, json={"email": email_of("admin"), "current_password": PASSWORD})
    assert taken.status_code == 409, taken.text

    changed = client.patch("/auth/me", headers=headers, json={"email": new_email, "current_password": PASSWORD})
    assert changed.status_code == 200, changed.text
    assert client.post("/auth/login", json={"email": new_email, "password": PASSWORD}, headers=MOBILE_APP).status_code == 200

    # Restore the seeded address so other tests can log in as sam.
    restore = client.patch("/auth/me", headers={"Authorization": headers["Authorization"]}, json={"email": me["email"], "current_password": PASSWORD})
    assert restore.status_code == 200, restore.text


def test_change_my_password(client, login):
    headers, _ = login("sam")

    wrong = client.post("/auth/change-password", headers=headers, json={"current_password": "nope123", "new_password": "Kerala2026"})
    assert wrong.status_code == 400, wrong.text

    weak = client.post("/auth/change-password", headers=headers, json={"current_password": PASSWORD, "new_password": "abcdefgh"})
    assert weak.status_code == 422, weak.text

    changed = client.post("/auth/change-password", headers=headers, json={"current_password": PASSWORD, "new_password": "Kerala2026"})
    assert changed.status_code == 200, changed.text
    assert client.post("/auth/login", json={"email": email_of("sam"), "password": "Kerala2026"}, headers=MOBILE_APP).status_code == 200
    assert client.post("/auth/login", json={"email": email_of("sam"), "password": PASSWORD}, headers=MOBILE_APP).status_code == 401

    # Restore the seeded password for later tests.
    back = client.post("/auth/change-password", headers=headers, json={"current_password": "Kerala2026", "new_password": PASSWORD})
    assert back.status_code == 200, back.text


# ---- Team tab (manager view) ---------------------------------------------------

# ---- Unauthenticated ---------------------------------------------------------

def test_settings_endpoints_require_a_token(client):
    assert client.get("/settings/enums").status_code == 401
    assert client.get("/settings/company").status_code == 401
    assert client.patch("/auth/me", json={"name": "x"}).status_code == 401
    assert client.post("/auth/change-password", json={"current_password": "x", "new_password": "Kerala2026"}).status_code == 401
