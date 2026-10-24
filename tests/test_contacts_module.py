"""Contacts module: consumer, partner and dealer rules, the duplicate phone rule, mobile-only fields,
option lists, and who can see which contacts."""
import pytest

from seed_data import PASSWORD, MOBILE_APP

MOBILE = {"X-Client": "mobile"}


def _contact(client, headers, source_id, **overrides):
    body = {
        "contact_type": "consumer", "name": "Arun Kumar", "phone": "9876543210",
        "district": "Calicut", "address": "House 4, MG Road", "lead_source_id": source_id,
        "consumer_profile": {"house_stage": "ready"},
    }
    body.update(overrides)
    return client.post("/contacts", json=body, headers=headers)


def _staff(client, admin_headers, email, role, phone):
    """Create an employee with any role through the API and return their login headers and id."""
    created = client.post("/employees", headers=admin_headers, json={
        "name": role.replace("_", " ").title(), "email": email, "password": PASSWORD, "role": role, "phone": phone,
    })
    assert created.status_code == 201, created.text
    login = client.post("/auth/login", json={"email": email, "password": PASSWORD}, headers=MOBILE_APP)
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}, created.json()["id"]


# --- type matrix ----------------------------------------------------------------------------

def test_each_type_creates_with_its_own_details(client, login, lead_source_id):
    headers, _ = login("admin")
    consumer = _contact(client, headers, lead_source_id("meta"), phone="9100000001")
    partner = _contact(client, headers, lead_source_id("meta"), phone="9100000002", contact_type="partner",
                       consumer_profile=None, partner_profile={"profession_type": "plumber"})
    dealer = _contact(client, headers, lead_source_id("meta"), phone="9100000003", contact_type="dealer",
                      name="Kerala Tanks", consumer_profile=None, dealer_profile={"owner_name": "Ravi"})
    assert consumer.status_code == 201, consumer.text
    assert partner.status_code == 201, partner.text
    assert dealer.status_code == 201, dealer.text
    assert dealer.json()["dealer_profile"]["owner_name"] == "Ravi"
    assert partner.json()["partner_profile"]["profession_type"] == "plumber"


def test_dealer_needs_owner_name_and_partner_does_not(client, login, lead_source_id):
    """Dealer requires dealer_profile.owner_name at creation. Partner profile is optional at creation."""
    headers, _ = login("admin")
    dealer = _contact(client, headers, lead_source_id("meta"), phone="9100000004", contact_type="dealer",
                      consumer_profile=None)
    assert dealer.status_code == 400 and "owner_name" in dealer.json()["detail"]
    # Partner can be created without a profile — it will be unverified until profession_type + work_area are added
    partner = _contact(client, headers, lead_source_id("meta"), phone="9100000005", contact_type="partner",
                       consumer_profile=None)
    assert partner.status_code == 201
    assert partner.json()["is_verified"] is False
    assert "profession_type" in partner.json()["missing_fields"]


def test_details_block_must_match_the_contact_type(client, login, lead_source_id):
    headers, _ = login("admin")
    response = _contact(client, headers, lead_source_id("meta"), phone="9100000006", contact_type="dealer",
                        dealer_profile={"owner_name": "Ravi"})  # still carries a consumer_profile
    assert response.status_code == 400
    assert "consumer_profile is only allowed" in response.json()["detail"]


def test_lead_source_and_phone_are_required(client, login, lead_source_id):
    headers, _ = login("admin")
    body = {"contact_type": "consumer", "name": "No source", "phone": "9100000007",
            "district": "Calicut", "address": "x"}
    assert client.post("/contacts", json=body, headers=headers).status_code == 422
    assert _contact(client, headers, None, phone="9100000008").status_code == 422


def test_invalid_phone_is_rejected(client, login, lead_source_id):
    headers, _ = login("admin")
    assert _contact(client, headers, lead_source_id("meta"), phone="12-ab").status_code == 422


# --- duplicate phone across all types --------------------------------------------------------

def test_same_phone_is_blocked_across_types(client, login, lead_source_id):
    headers, _ = login("admin")
    assert _contact(client, headers, lead_source_id("meta"), phone="9100000010").status_code == 201
    response = _contact(client, headers, lead_source_id("meta"), phone="9100000010", contact_type="dealer",
                        name="Shop", consumer_profile=None, dealer_profile={"owner_name": "Ravi"})
    assert response.status_code == 409
    assert "contact" in response.json()["detail"]  # admins see which contact


def test_country_code_and_spacing_do_not_hide_a_duplicate(client, login, lead_source_id):
    headers, _ = login("admin")
    assert _contact(client, headers, lead_source_id("meta"), phone="9100000011").status_code == 201
    assert _contact(client, headers, lead_source_id("meta"), phone="+91 91000 00011").status_code == 409


def test_deleted_contact_frees_its_phone(client, login, lead_source_id):
    headers, _ = login("admin")
    first = _contact(client, headers, lead_source_id("meta"), phone="9100000012").json()
    assert client.delete(f"/contacts/{first['id']}", headers=headers).status_code == 204
    assert _contact(client, headers, lead_source_id("meta"), phone="9100000012").status_code == 201


def test_edit_to_another_contacts_phone_is_blocked_and_keeping_your_own_is_allowed(client, login, lead_source_id):
    headers, _ = login("admin")
    _contact(client, headers, lead_source_id("meta"), phone="9100000013")
    other = _contact(client, headers, lead_source_id("meta"), phone="9100000014", name="Other").json()
    assert client.put(f"/contacts/{other['id']}", json={"phone": "9100000013"}, headers=headers).status_code == 409
    assert client.put(f"/contacts/{other['id']}", json={"phone": "9100000014"}, headers=headers).status_code == 200


# --- mobile-only fields ----------------------------------------------------------------------

def test_web_can_create_without_address_and_district(client, login, lead_source_id):
    headers, _ = login("admin")
    response = _contact(client, headers, lead_source_id("meta"), phone="9100000020", address=None, district=None)
    assert response.status_code == 201, response.text
    assert response.json()["is_verified"] is False  # the details are still pending, so the lead shows what is missing


def test_mobile_can_create_without_address_and_district(client, login, lead_source_id):
    """Mobile app no longer hard-requires address and district on create; contact is unverified until filled."""
    headers, _ = login("admin")
    response = _contact(client, {**headers, **MOBILE}, lead_source_id("meta"), phone="9100000021",
                        address=None, district=None)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["is_verified"] is False
    assert "district" in body["missing_fields"]
    assert "address" in body["missing_fields"]


def test_mobile_cannot_clear_address_on_edit(client, login, lead_source_id):
    headers, _ = login("admin")
    contact = _contact(client, headers, lead_source_id("meta"), phone="9100000022").json()
    response = client.put(f"/contacts/{contact['id']}", json={"address": ""}, headers={**headers, **MOBILE})
    assert response.status_code == 422  # an empty address fails the minimum length, so it cannot be cleared


# --- option lists (house stage and tank type come from Settings) ----------------------------

def _option(client, headers, category, value):
    rows = client.get(f"/settings/enums/{category}", headers=headers).json()
    return next(r for r in rows if r["value"] == value)


def test_house_stage_and_tank_type_must_be_settings_options(client, login, lead_source_id):
    headers, _ = login("admin")
    bad_stage = _contact(client, headers, lead_source_id("meta"), phone="9100000030",
                         consumer_profile={"house_stage": "Under Construction - Structure"})
    assert bad_stage.status_code == 422
    bad_tank = _contact(client, headers, lead_source_id("meta"), phone="9100000031",
                        consumer_profile={"house_stage": "ready", "current_tank_type": "Pvc"})
    assert bad_tank.status_code == 422
    good = _contact(client, headers, lead_source_id("meta"), phone="9100000032",
                    consumer_profile={"house_stage": "under_construction", "current_tank_type": "plastic",
                                      "current_tank_capacity": 1000})
    assert good.status_code == 201, good.text


def test_partner_profession_is_a_fixed_value(client, login, lead_source_id):
    headers, _ = login("admin")
    response = _contact(client, headers, lead_source_id("meta"), phone="9100000033", contact_type="partner",
                        consumer_profile=None, partner_profile={"profession_type": "welder"})
    assert response.status_code == 422


def test_option_lists_are_seeded_in_settings(client, login):
    headers, _ = login("admin")
    stages = [s["value"] for s in client.get("/settings/enums/house_stage", headers=headers).json()]
    assert stages == ["ready", "under_construction", "renovation"]
    tanks = [t["value"] for t in client.get("/settings/enums/tank_type", headers=headers).json()]
    assert tanks == ["plastic", "concrete", "steel", "other"]
    professions = [p["value"] for p in client.get("/contacts/partner-types", headers=headers).json()]
    assert "plumber" in professions and "other" in professions


def test_old_fixed_list_endpoints_are_gone(client, login):
    headers, _ = login("admin")
    for path in ("/contacts/house-stages", "/contacts/tank-types"):
        assert client.get(path, headers=headers).status_code in (404, 422)  # now only matches /contacts/{id}


def test_admin_added_tank_type_is_accepted(client, login, lead_source_id):
    headers, _ = login("admin")
    added = client.post("/settings/enums/tank_type", json={"label": "PVC"}, headers=headers)
    assert added.status_code == 201, added.text
    assert added.json()["value"] == "pvc"
    created = _contact(client, headers, lead_source_id("meta"), phone="9100000040",
                       consumer_profile={"house_stage": "ready", "current_tank_type": "pvc"})
    assert created.status_code == 201, created.text
    assert created.json()["consumer_profile"]["current_tank_type"] == "pvc"
    pvc = next(r for r in client.get("/settings/enums", headers=headers).json()["tank_type"] if r["value"] == "pvc")
    assert pvc["usage_count"] == 1  # Settings shows how many contacts use it


def test_hidden_house_stage_is_kept_on_old_records_but_not_offered(client, login, lead_source_id):
    headers, _ = login("admin")
    old = _contact(client, headers, lead_source_id("meta"), phone="9100000041",
                   consumer_profile={"house_stage": "renovation"}).json()
    renovation = _option(client, headers, "house_stage", "renovation")
    hidden = client.patch(f"/settings/enums/house_stage/{renovation['id']}", json={"is_active": False}, headers=headers)
    assert hidden.status_code == 200, hidden.text
    assert "renovation" not in [r["value"] for r in client.get("/settings/enums/house_stage", headers=headers).json()]

    blocked = _contact(client, headers, lead_source_id("meta"), phone="9100000042",
                       consumer_profile={"house_stage": "renovation"})
    assert blocked.status_code == 422
    # Editing the old record, even re-sending its current stage, still works.
    edited = client.put(f"/contacts/{old['id']}", headers=headers,
                        json={"name": "Renamed", "consumer_profile": {"house_stage": "renovation"}})
    assert edited.status_code == 200, edited.text
    assert edited.json()["consumer_profile"]["house_stage"] == "renovation"


def test_blank_house_stage_clears_it(client, login, lead_source_id):
    headers, _ = login("admin")
    contact = _contact(client, headers, lead_source_id("meta"), phone="9100000043").json()
    cleared = client.put(f"/contacts/{contact['id']}", headers=headers, json={"consumer_profile": {"house_stage": ""}})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["consumer_profile"]["house_stage"] is None
    assert "house_stage" in cleared.json()["missing_fields"]


def test_awareness_mode_is_gone_from_contacts(client, login, lead_source_id):
    headers, _ = login("admin")
    response = _contact(client, headers, lead_source_id("meta"), phone="9100000034",
                        consumer_profile={"awareness_mode": 1, "house_stage": "ready"})
    assert response.status_code == 201
    assert "awareness_mode" not in response.json()["consumer_profile"]  # awareness lives in the lead stages now


def test_dealer_appears_in_list_with_owner_name(client, login, lead_source_id):
    headers, _ = login("admin")
    _contact(client, headers, lead_source_id("meta"), phone="9100000035", contact_type="dealer",
             name="Kerala Tanks", consumer_profile=None, dealer_profile={"owner_name": "Ravi"})
    rows = client.get("/contacts", params={"contact_type": "dealer"}, headers=headers).json()["items"]
    assert [(r["name"], r["owner_name"]) for r in rows] == [("Kerala Tanks", "Ravi")]


def test_list_row_shows_who_created_it_and_their_branch(client, login, lead_source_id, staff):
    """Each list row names the creator and the creator's branch, so admins can see it at a glance."""
    admin = staff["admin"]
    anu_headers, anu_id = staff["anu"]
    assert client.put(f"/employees/{anu_id}", headers=admin, json={"branch": "kottakkal"}).status_code == 200
    _contact(client, anu_headers, lead_source_id("meta"), phone="9100000050", name="By Anu")
    _contact(client, admin, lead_source_id("meta"), phone="9100000051", name="By Admin")
    rows = {r["name"]: r for r in client.get("/contacts", headers=admin).json()["items"]}
    assert rows["By Anu"]["created_by"]["name"] == "Anu Sales"
    assert rows["By Anu"]["created_by_branch"] == "kottakkal"
    assert rows["By Admin"]["created_by_branch"] is None


# --- who can see which contacts --------------------------------------------------------------

@pytest.fixture()
def staff(client, login):
    admin, _ = login("admin")
    other, other_id = _staff(client, admin, "other@example.com", "sales_executive", "9000000010")
    anu, anu_user = login("anu")
    raj, raj_user = login("raj")
    return {
        "admin": admin, "other": (other, other_id),
        "anu": (anu, anu_user["id"]), "raj": (raj, raj_user["id"]),
    }


def test_admin_and_manager_see_every_contact(client, login, lead_source_id, staff):
    _contact(client, staff["anu"][0], lead_source_id("meta"), phone="9100000040")
    manager, _ = login("manager")
    assert client.get("/contacts", headers=staff["admin"]).json()["total"] == 1
    assert client.get("/contacts", headers=manager).json()["total"] == 1


# --- Sprint 2: auto follow-up on contact creation (48h rule) --------------------------------

def test_staff_contact_creation_auto_schedules_first_followup(client, login, lead_source_id):
    """When a sales_executive creates a contact, a follow-up is auto-created at +48h.

    WHY THIS MATTERS:
        Staff who create a contact must not forget to follow up. The backend silently schedules
        the first call so nothing slips through the cracks even if the employee forgets.
    """
    headers, _ = login("anu")  # anu is a sales_executive
    source_id = lead_source_id("meta")
    res = _contact(client, headers, source_id, phone="9900000050")
    assert res.status_code == 201, res.text
    data = res.json()
    # auto_followup_needed should be False for staff (backend handled it silently)
    assert data["auto_followup_needed"] is False
    # The follow-up must exist for the newly created contact
    contact_id = data["id"]
    admin_headers, _ = login("admin")
    followups = client.get(f"/contacts/{contact_id}/followups", headers=admin_headers).json()
    assert len(followups) == 1
    fu = followups[0]
    assert fu["title"] == "First Contact"
    assert fu["priority"] == "high"
    assert fu["channel"] == "phone_call"
    assert fu["status"] == "todo"


def test_admin_contact_creation_sets_auto_followup_needed(client, login, lead_source_id):
    """When admin/manager creates a contact, no follow-up is auto-created and the flag is True.

    WHY THIS MATTERS:
        Admin and manager don't carry leads (rule 1 in rule.md). When they create a contact
        they need to assign the first follow-up to a staff member manually. The flag tells the
        web app to show a prompt modal for this.
    """
    headers, _ = login("admin")
    res = _contact(client, headers, lead_source_id("meta"), phone="9900000051")
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["auto_followup_needed"] is True
    # No follow-up should have been auto-created
    followups = client.get(f"/contacts/{data['id']}/followups", headers=headers).json()
    assert followups == []
