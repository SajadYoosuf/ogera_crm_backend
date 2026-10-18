"""Settings screen: dropdown options, company profile, permission matrix, and my account.

Who may do what (routes/settings.py, routes/auth.py):
- Dropdown options: admin and manager read and change them; everyone signed in reads the active ones.
- Company profile: admin changes it; admin and manager read it.
- Permission matrix: admin and manager read it and switch staff roles' actions. Admin is never switched off.
  Only an admin changes what managers may do.
- My account: anyone signed in edits their own name, email (with the current password) and password.
"""
from seed_data import PASSWORD, email_of


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
    for key in ("admin", "manager", "anu", "raj", "tara", "shiny", "sam"):
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


def test_company_write_is_admin_only_manager_is_refused(client, login):
    admin, manager = _admin(login), _manager(login)
    original = client.get("/settings/company", headers=admin).json()["company_name"]

    denied = client.patch("/settings/company", headers=manager, json={"company_name": "Manager Edit Co"})
    assert denied.status_code == 403, denied.text

    updated = client.patch("/settings/company", headers=admin, json={"company_name": "Ogera Water Tanks"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["company_name"] == "Ogera Water Tanks"

    # Restore, so this test doesn't leak state into others that read the company name.
    client.patch("/settings/company", headers=admin, json={"company_name": original})


def test_staff_cannot_read_or_write_company(client, login):
    staff = _staff(login)
    assert client.get("/settings/company", headers=staff).status_code == 403
    assert client.patch("/settings/company", headers=staff, json={"company_name": "Hacked"}).status_code == 403


# ---- Permission matrix (Roles tab) -----------------------------------------

def _cell(matrix, feature_key, role_slug):
    feature = next(f for f in matrix["features"] if f["key"] == feature_key)
    return feature["levels"][role_slug]


def test_permission_matrix_is_manager_and_admin_only(client, login):
    assert client.get("/settings/roles", headers=_admin(login)).status_code == 200
    assert client.get("/settings/roles", headers=_manager(login)).status_code == 200
    assert client.get("/settings/roles", headers=_staff(login)).status_code == 403


def test_every_seeded_role_appears_in_the_matrix(client, login):
    matrix = client.get("/settings/roles", headers=_admin(login)).json()
    roles_seen = {level_key for feature in matrix["features"] for level_key in feature["levels"]}
    for role in ("admin", "manager", "team_lead", "sales_executive", "tele_caller", "field_executive", "support"):
        assert role in roles_seen, f"{role} missing from the permission matrix"


def test_admin_cells_are_never_editable_and_manager_cells_only_for_admin(client, login):
    admin_view = client.get("/settings/roles", headers=_admin(login)).json()
    manager_view = client.get("/settings/roles", headers=_manager(login)).json()

    assert _cell(admin_view, "export", "admin")["editable"] is False
    assert _cell(admin_view, "export", "manager")["editable"] is True
    assert _cell(manager_view, "export", "manager")["editable"] is False
    assert _cell(manager_view, "export", "team_lead")["editable"] is True
    # Fixed rows (employees, settings) have no switch at all.
    assert _cell(manager_view, "employees", "sales_executive")["editable"] is False


def test_turning_an_action_off_takes_effect_at_once_and_on_again_restores_it(client, login):
    admin, manager = _admin(login), _manager(login)
    team_lead, _ = login("tara")

    assert client.get("/contacts/export?format=csv", headers=team_lead).status_code == 200

    off = client.put("/settings/roles/team_lead/permissions/export", headers=manager, json={"allowed": False})
    assert off.status_code == 200, off.text
    assert _cell(off.json(), "export", "team_lead")["level"] == "none"
    assert client.get("/contacts/export?format=csv", headers=team_lead).status_code == 403

    on = client.put("/settings/roles/team_lead/permissions/export", headers=admin, json={"allowed": True})
    assert on.status_code == 200, on.text
    assert client.get("/contacts/export?format=csv", headers=team_lead).status_code == 200


def test_manager_cannot_change_manager_or_admin_permissions(client, login):
    manager = _manager(login)
    assert client.put("/settings/roles/manager/permissions/export", headers=manager, json={"allowed": False}).status_code == 403
    assert client.put("/settings/roles/admin/permissions/export", headers=manager, json={"allowed": False}).status_code == 403


def test_admin_keeps_every_permission_and_manager_is_changed_only_by_admin(client, login):
    admin = _admin(login)
    assert client.put("/settings/roles/admin/permissions/export", headers=admin, json={"allowed": False}).status_code == 403

    changed = client.put("/settings/roles/manager/permissions/export", headers=admin, json={"allowed": False})
    assert changed.status_code == 200, changed.text
    assert _cell(changed.json(), "export", "manager")["level"] == "none"

    restored = client.put("/settings/roles/manager/permissions/export", headers=admin, json={"allowed": True})
    assert restored.status_code == 200, restored.text


def test_staff_cannot_change_permissions(client, login):
    staff = _staff(login)
    assert client.put("/settings/roles/team_lead/permissions/export", headers=staff, json={"allowed": False}).status_code == 403


def test_unknown_action_or_role_is_404(client, login):
    manager = _manager(login)
    assert client.put("/settings/roles/team_lead/permissions/not_an_action", headers=manager, json={"allowed": True}).status_code == 404
    assert client.put("/settings/roles/no_such_role/permissions/export", headers=manager, json={"allowed": True}).status_code == 404


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
    assert client.post("/auth/login", json={"email": new_email, "password": PASSWORD}).status_code == 200

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
    assert client.post("/auth/login", json={"email": email_of("sam"), "password": "Kerala2026"}).status_code == 200
    assert client.post("/auth/login", json={"email": email_of("sam"), "password": PASSWORD}).status_code == 401

    # Restore the seeded password for later tests.
    back = client.post("/auth/change-password", headers=headers, json={"current_password": "Kerala2026", "new_password": PASSWORD})
    assert back.status_code == 200, back.text


# ---- Team tab (manager view) ---------------------------------------------------

def test_manager_team_list_hides_admin_and_other_managers(client, login):
    listed = client.get("/employees?limit=100", headers=_manager(login))
    assert listed.status_code == 200, listed.text
    roles = {person["role"] for person in listed.json()["items"]}
    assert "admin" not in roles
    assert "manager" not in roles
    assert "sales_executive" in roles


# ---- Unauthenticated ---------------------------------------------------------

def test_settings_endpoints_require_a_token(client):
    assert client.get("/settings/enums").status_code == 401
    assert client.get("/settings/company").status_code == 401
    assert client.get("/settings/roles").status_code == 401
    assert client.patch("/auth/me", json={"name": "x"}).status_code == 401
    assert client.post("/auth/change-password", json={"current_password": "x", "new_password": "Kerala2026"}).status_code == 401
