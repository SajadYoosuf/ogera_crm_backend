"""An admin changing an employee's email must move the public login lookup too.

Login finds the account through public.employee_tenant_map, so after the change the
employee must be able to sign in with the new address, and the old one must stop working.
"""
from seed_data import PASSWORD, email_of, MOBILE_APP


def _admin(login):
    headers, _ = login("admin")
    return headers


def test_admin_email_change_moves_login_to_new_address(client, login):
    admin = _admin(login)
    _, sam = login("sam")
    old_email = sam["email"]
    new_email = "sam.admin.moved@example.com"

    changed = client.put(f"/employees/{sam['id']}", headers=admin, json={"email": new_email})
    assert changed.status_code == 200, changed.text
    assert changed.json()["email"] == new_email

    assert client.post("/auth/login", json={"email": new_email, "password": PASSWORD}, headers=MOBILE_APP).status_code == 200
    assert client.post("/auth/login", json={"email": old_email, "password": PASSWORD}, headers=MOBILE_APP).status_code == 401

    # Put the seeded address back so other tests can sign in as sam.
    restore = client.put(f"/employees/{sam['id']}", headers=admin, json={"email": old_email})
    assert restore.status_code == 200, restore.text
    assert client.post("/auth/login", json={"email": email_of("sam"), "password": PASSWORD}, headers=MOBILE_APP).status_code == 200


def test_admin_email_change_to_an_address_in_use_is_refused(client, login):
    admin = _admin(login)
    _, sam = login("sam")

    taken = client.put(f"/employees/{sam['id']}", headers=admin, json={"email": email_of("admin")})
    assert taken.status_code == 409, taken.text
    assert client.post("/auth/login", json={"email": email_of("sam"), "password": PASSWORD}, headers=MOBILE_APP).status_code == 200
