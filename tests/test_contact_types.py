"""Per-type rules: verification, optional fields, profile endpoints, name-optional across all types."""


def _make(client, headers, source_id, **overrides):
    body = {
        "contact_type": "consumer",
        "name": "Test Contact",
        "phone": "9700000000",
        "lead_source_id": source_id,
        "district": "Calicut",
        "address": "MG Road",
        "consumer_profile": {"house_stage": "ready"},
    }
    body.update(overrides)
    return client.post("/contacts", json=body, headers=headers)


# --- name is optional for every contact type ------------------------------------------------

def test_partner_without_name_succeeds(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"),
              phone="9700000001", name=None, contact_type="partner",
              consumer_profile=None,
              partner_profile={"profession_type": "plumber"})
    assert r.status_code == 201, r.text
    assert r.json()["name"] is None


# --- consumer verification ------------------------------------------------------------------

def test_consumer_is_verified_when_all_fields_are_present(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9700000010").json()
    assert r["is_verified"] is True
    assert r["missing_fields"] == []


def test_consumer_missing_fields_when_optional_fields_omitted(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9700000011",
              district=None, address=None, consumer_profile=None).json()
    assert set(r["missing_fields"]) == {"district", "address", "house_stage"}


def test_consumer_unverified_when_house_stage_missing(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9700000012",
              consumer_profile=None).json()
    assert r["is_verified"] is False
    assert "house_stage" in r["missing_fields"]


# --- partner verification -------------------------------------------------------------------

def test_partner_is_verified_when_all_fields_present(client, login, lead_source_id):
    """Partners need name + profession_type + work_area + address + district to be verified."""
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9700000020",
              contact_type="partner", name="Arun Kumar", consumer_profile=None,
              partner_profile={"profession_type": "plumber", "work_area": "Calicut"}).json()
    assert r["is_verified"] is True
    assert r["missing_fields"] == []


def test_partner_unverified_when_name_missing(client, login, lead_source_id):
    """Any contact without a name is unverified."""
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9700000021",
              name=None, contact_type="partner", consumer_profile=None,
              partner_profile={"profession_type": "plumber", "work_area": "Calicut"}).json()
    assert r["is_verified"] is False
    assert "name" in r["missing_fields"]


def test_partner_unverified_when_work_area_missing(client, login, lead_source_id):
    """Partner with only profession_type is unverified until work_area is also filled."""
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9700000022",
              contact_type="partner", name="Arun Kumar", consumer_profile=None,
              partner_profile={"profession_type": "plumber"}).json()
    assert r["is_verified"] is False
    assert "work_area" in r["missing_fields"]
    assert "house_stage" not in r["missing_fields"]


def test_partner_unverified_when_district_missing(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9700000023",
              contact_type="partner", name="Arun Kumar", district=None, address=None,
              consumer_profile=None,
              partner_profile={"profession_type": "plumber", "work_area": "Calicut"}).json()
    assert r["is_verified"] is False
    assert "district" in r["missing_fields"]
    assert "house_stage" not in r["missing_fields"]


# --- partner profile optional fields -------------------------------------------------------

def test_partner_profile_optional_fields_are_saved(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9700000040",
              contact_type="partner", consumer_profile=None,
              partner_profile={
                  "profession_type": "architect",
                  "work_area": "Kozhikode",
              }).json()
    assert r["partner_profile"]["work_area"] == "Kozhikode"
    assert r["partner_profile"]["profession_type"] == "architect"


def test_partner_profile_update_changes_only_sent_fields(client, login, lead_source_id):
    headers, _ = login("admin")
    contact = _make(client, headers, lead_source_id("meta"), phone="9700000041",
                    contact_type="partner", consumer_profile=None,
                    partner_profile={"profession_type": "plumber", "work_area": "Feroke"}).json()
    updated = client.put(f"/contacts/{contact['id']}",
                         json={"partner_profile": {"work_area": "Calicut"}},
                         headers=headers).json()
    assert updated["partner_profile"]["profession_type"] == "plumber"  # unchanged
    assert updated["partner_profile"]["work_area"] == "Calicut"         # updated



