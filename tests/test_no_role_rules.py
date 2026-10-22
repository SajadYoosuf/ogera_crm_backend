"""After role-based access was removed: what every signed-in employee can do, and the rules that stay.

Stays: the website is for admin and manager only, and only they can manage employees and settings.
Everything else is open to any signed-in employee.
"""
from seed_data import MOBILE_APP, PASSWORD, email_of


def _contact(client, headers, lead_source_id, phone, assigned_to=None):
    body = {"name": "Test Lead", "phone": phone, "lead_source_id": lead_source_id("meta"),
            "address": "Main road", "district": "Kozhikode"}
    if assigned_to is not None:
        body["assigned_to"] = assigned_to
    response = client.post("/contacts", json=body, headers={**headers, **MOBILE_APP})
    assert response.status_code == 201, response.text
    return response.json()


# ---- sign-in: which app each role uses -----------------------------------------------------

def test_sales_executive_cannot_sign_in_on_the_website(client):
    response = client.post("/auth/login", json={"email": email_of("anu"), "password": PASSWORD})
    assert response.status_code == 403
    assert "mobile app" in response.json()["detail"]


def test_sales_executive_signs_in_on_the_mobile_app(client):
    response = client.post("/auth/login", json={"email": email_of("anu"), "password": PASSWORD}, headers=MOBILE_APP)
    assert response.status_code == 200, response.text


def test_older_mobile_header_also_counts_as_the_mobile_app(client):
    response = client.post("/auth/login", json={"email": email_of("anu"), "password": PASSWORD}, headers={"X-Client": "mobile"})
    assert response.status_code == 200, response.text


def test_admin_and_manager_sign_in_on_the_website(client):
    for key in ("admin", "manager"):
        response = client.post("/auth/login", json={"email": email_of(key), "password": PASSWORD})
        assert response.status_code == 200, f"{key}: {response.text}"


def test_wrong_password_on_the_website_is_still_401_not_a_hint(client):
    # A wrong password must not reveal that the account is a sales executive.
    response = client.post("/auth/login", json={"email": email_of("anu"), "password": "WrongPass9!"})
    assert response.status_code == 401


# ---- open to everyone signed in ------------------------------------------------------------

def test_any_employee_sees_and_edits_every_contact(client, login, lead_source_id):
    admin, _ = login("admin")
    anu, _ = login("anu")
    _, raj = login("raj")
    contact = _contact(client, admin, lead_source_id, "9100000901", assigned_to=raj["id"])  # not Anu's

    assert client.get("/contacts", headers=anu).json()["total"] == 1
    assert client.get(f"/contacts/{contact['id']}", headers=anu).status_code == 200
    edited = client.put(f"/contacts/{contact['id']}", json={"notes": "Called"}, headers=anu)
    assert edited.status_code == 200, edited.text


def test_duplicate_phone_message_names_the_other_contact_for_everyone(client, login, lead_source_id):
    admin, _ = login("admin")
    anu, _ = login("anu")
    first = _contact(client, admin, lead_source_id, "9100000902")
    clash = client.post("/contacts", json={"name": "Again", "phone": "9100000902", "lead_source_id": lead_source_id("meta"),
                                           "address": "x", "district": "Kozhikode"}, headers={**anu, **MOBILE_APP})
    assert clash.status_code == 409
    assert f"contact {first['id']}" in clash.json()["detail"]


def test_any_employee_can_export_contacts(client, login):
    anu, _ = login("anu")
    assert client.get("/contacts/export?format=csv", headers=anu).status_code == 200


def test_any_employee_can_add_a_sale_and_a_product(client, login):
    anu, _ = login("anu")
    assert client.post("/sales", json={"name": "Ravi"}, headers=anu).status_code == 201
    product = client.post("/products", json={"name": "1000 L tank", "slug": "1000-l-tank"}, headers=anu)
    assert product.status_code == 201, product.text


def test_follow_up_can_be_assigned_to_a_manager(client, login, lead_source_id):
    import datetime
    admin, _ = login("admin")
    _, manager = login("manager")
    contact = _contact(client, admin, lead_source_id, "9100000903")
    due = (datetime.datetime.now() + datetime.timedelta(days=2)).replace(microsecond=0).isoformat()
    response = client.post(f"/contacts/{contact['id']}/followups",
                           json={"assigned_to": manager["id"], "due_at": due, "channel": "phone_call"}, headers=admin)
    assert response.status_code == 201, response.text


def test_employee_list_shows_admins_and_managers(client, login):
    anu, _ = login("anu")
    roles = {person["role"] for person in client.get("/employees?limit=100", headers=anu).json()["items"]}
    assert {"admin", "manager", "sales_executive"} <= roles


# ---- the one guard that stays --------------------------------------------------------------

def test_sales_executive_cannot_manage_employees(client, login):
    anu, me = login("anu")
    _, raj = login("raj")
    body = {"name": "New", "email": "new.person@example.com", "password": "Kerala2026", "role": "sales_executive"}
    assert client.post("/employees", json=body, headers=anu).status_code == 403
    assert client.put(f"/employees/{raj['id']}", json={"name": "X"}, headers=anu).status_code == 403
    assert client.delete(f"/employees/{raj['id']}", headers=anu).status_code == 403


def test_sales_executive_cannot_change_settings_or_roles(client, login):
    anu, _ = login("anu")
    assert client.patch("/settings/company", json={"company_name": "X"}, headers=anu).status_code == 403
    assert client.post("/settings/enums/district", json={"label": "Wayanad"}, headers=anu).status_code == 403
    assert client.post("/roles", json={"slug": "intern", "name": "Intern"}, headers=anu).status_code == 403


def test_manager_can_do_what_admin_can(client, login):
    manager, _ = login("manager")
    assert client.patch("/settings/company", json={"company_name": "Ogera Water Tanks"}, headers=manager).status_code == 200
    created = client.post("/roles", json={"slug": "intern", "name": "Intern"}, headers=manager)
    assert created.status_code == 201, created.text


def test_permission_matrix_is_gone(client, login):
    admin, _ = login("admin")
    assert client.get("/settings/roles", headers=admin).status_code == 404
