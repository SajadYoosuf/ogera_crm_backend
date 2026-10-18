"""Partner types: the profession dropdown in Settings > Lists & Options (`professional_type`).

- Seeded with the original seven; `other` is locked (imports fall back to it).
- Admin and manager add, rename, hide and reorder; anyone signed in reads the active ones.
- A new partner must pick an active type. A hidden type stays on partners who already have it.
"""
from test_settings import _admin, _manager, _staff


def _contact(client, headers, source_id, phone, profession, contact_type="partner"):
    body = {
        "contact_type": contact_type, "name": "Partner Test", "phone": phone,
        "district": "Calicut", "address": "Shop 1", "lead_source_id": source_id,
        "partner_profile": {"profession_type": profession},
    }
    return client.post("/contacts", json=body, headers=headers)


def _category(client, headers):
    response = client.get("/settings/enums/professional_type", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_partner_types_are_seeded_with_other_locked(client, login):
    rows = _category(client, _admin(login))
    values = [row["value"] for row in rows]
    assert values == ["plumber", "engineer", "contractor", "interior_designer", "architect", "electrician", "other"]
    other = next(row for row in rows if row["value"] == "other")
    assert other["locked"] is True


def test_partner_types_list_matches_settings_and_is_open_to_staff(client, login):
    listed = client.get("/contacts/partner-types", headers=_staff(login))
    assert listed.status_code == 200, listed.text
    assert [row["value"] for row in listed.json()][:2] == ["plumber", "engineer"]


def test_admin_adds_a_partner_type_and_a_partner_can_use_it(client, login, lead_source_id):
    admin = _admin(login)
    created = client.post("/settings/enums/professional_type", headers=admin, json={"value": "surveyor", "label": "Surveyor"})
    assert created.status_code == 201, created.text

    offered = client.get("/contacts/partner-types", headers=admin).json()
    assert {"value": "surveyor", "label": "Surveyor"} in offered

    response = _contact(client, admin, lead_source_id("meta"), "9100000201", "surveyor")
    assert response.status_code == 201, response.text
    assert response.json()["partner_profile"]["profession_type"] == "surveyor"


def test_manager_can_add_but_sales_executive_cannot(client, login):
    denied = client.post("/settings/enums/professional_type", headers=_staff(login), json={"value": "carpenter", "label": "Carpenter"})
    assert denied.status_code == 403, denied.text
    allowed = client.post("/settings/enums/professional_type", headers=_manager(login), json={"value": "carpenter", "label": "Carpenter"})
    assert allowed.status_code == 201, allowed.text


def test_free_text_profession_is_normalised_to_the_stored_key(client, login, lead_source_id):
    admin = _admin(login)
    response = _contact(client, admin, lead_source_id("meta"), "9100000202", "Interior Designer")
    assert response.status_code == 201, response.text
    assert response.json()["partner_profile"]["profession_type"] == "interior_designer"


def test_unknown_profession_is_rejected_for_a_new_partner(client, login, lead_source_id):
    response = _contact(client, _admin(login), lead_source_id("meta"), "9100000203", "astronaut")
    assert response.status_code == 422, response.text


def test_hidden_type_is_refused_for_new_partners_but_kept_on_existing_ones(client, login, lead_source_id):
    admin = _admin(login)
    created = client.post("/settings/enums/professional_type", headers=admin, json={"value": "glazier", "label": "Glazier"}).json()
    existing = _contact(client, admin, lead_source_id("meta"), "9100000204", "glazier")
    assert existing.status_code == 201, existing.text
    contact_id = existing.json()["id"]

    hidden = client.patch(f"/settings/enums/professional_type/{created['id']}", headers=admin, json={"is_active": False})
    assert hidden.status_code == 200, hidden.text

    refused = _contact(client, admin, lead_source_id("meta"), "9100000205", "glazier")
    assert refused.status_code == 422, refused.text

    # Editing the existing partner without changing the hidden type still works.
    kept = client.put(f"/contacts/{contact_id}", headers=admin,
                        json={"partner_profile": {"profession_type": "glazier", "work_area": "Feroke"}})
    assert kept.status_code == 200, kept.text
    assert kept.json()["partner_profile"]["profession_type"] == "glazier"


def _usage(client, headers):
    # The admin list carries usage counts; the dropdown endpoint does not.
    rows = client.get("/settings/enums", headers=headers).json()["professional_type"]
    return {row["value"]: row["usage_count"] for row in rows}


def test_usage_count_counts_partners_on_each_type(client, login, lead_source_id):
    admin = _admin(login)
    before = _usage(client, admin)
    assert _contact(client, admin, lead_source_id("meta"), "9100000206", "architect").status_code == 201
    assert _usage(client, admin)["architect"] == before["architect"] + 1


def test_other_cannot_be_hidden(client, login):
    admin = _admin(login)
    other = next(row for row in _category(client, admin) if row["value"] == "other")
    response = client.patch(f"/settings/enums/professional_type/{other['id']}", headers=admin, json={"is_active": False})
    assert response.status_code >= 400, response.text
    assert next(row for row in _category(client, admin) if row["value"] == "other")["is_active"] is True
