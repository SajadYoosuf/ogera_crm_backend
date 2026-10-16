"""Per-type rules: verification, optional fields, profile endpoints, name-optional across all types."""


def _make(client, headers, source_id, **overrides):
    body = {
        "contact_type": "consumer",
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
              phone="9700000001", contact_type="partner",
              consumer_profile=None,
              partner_profile={"profession_type": "plumber"})
    assert r.status_code == 201, r.text
    assert r.json()["name"] is None


def test_dealer_without_name_succeeds(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"),
              phone="9700000002", contact_type="dealer",
              consumer_profile=None,
              dealer_profile={"owner_name": "Ravi"})
    assert r.status_code == 201, r.text
    assert r.json()["name"] is None


# --- consumer verification ------------------------------------------------------------------

def test_consumer_is_verified_when_all_fields_are_present(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9700000010").json()
    assert r["is_verified"] is True
    assert r["missing_fields"] == []


def test_consumer_missing_fields_lists_only_non_name_gaps(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9700000011",
              district=None, address=None, consumer_profile=None).json()
    assert "name" not in r["missing_fields"]
    assert set(r["missing_fields"]) == {"district", "address", "house_stage"}


def test_consumer_unverified_when_house_stage_missing(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9700000012",
              consumer_profile=None).json()
    assert r["is_verified"] is False
    assert "house_stage" in r["missing_fields"]


# --- partner verification -------------------------------------------------------------------

def test_partner_is_verified_without_house_stage(client, login, lead_source_id):
    """Partners have no house_stage requirement — house_stage is consumer-only."""
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9700000020",
              contact_type="partner", consumer_profile=None,
              partner_profile={"profession_type": "plumber"}).json()
    assert r["is_verified"] is True
    assert r["missing_fields"] == []


def test_partner_unverified_when_district_missing(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9700000021",
              contact_type="partner", district=None, address=None,
              consumer_profile=None,
              partner_profile={"profession_type": "plumber"}).json()
    assert r["is_verified"] is False
    assert "district" in r["missing_fields"]
    assert "house_stage" not in r["missing_fields"]


# --- dealer verification --------------------------------------------------------------------

def test_dealer_verified_when_owner_name_present(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9700000030",
              contact_type="dealer", consumer_profile=None,
              dealer_profile={"owner_name": "Ravi"}).json()
    assert r["is_verified"] is True
    assert r["missing_fields"] == []


def test_dealer_unverified_missing_fields_includes_owner_name(client, login, lead_source_id):
    """Dealer profile can be added later; without it owner_name is missing."""
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9700000031",
              contact_type="dealer", consumer_profile=None,
              dealer_profile={"owner_name": "Ravi"}).json()
    # Remove dealer profile via PUT to simulate a dealer with no profile
    client.put(f"/contacts/{r['id']}", json={"dealer_profile": {"owner_name": "Ravi"}}, headers=headers)
    # Check via GET
    fetched = client.get(f"/contacts/{r['id']}", headers=headers).json()
    assert fetched["is_verified"] is True  # owner_name is still there

    # A dealer created without dealer_profile (would fail validation — confirmed separately)
    # so directly check missing_fields when profile is absent via DB
    from database import SessionLocal
    from models import DealerProfile
    with SessionLocal() as db:
        dp = db.query(DealerProfile).filter(DealerProfile.contact_id == r["id"]).first()
        db.delete(dp)
        db.commit()

    fetched2 = client.get(f"/contacts/{r['id']}", headers=headers).json()
    assert fetched2["is_verified"] is False
    assert "owner_name" in fetched2["missing_fields"]


# --- partner profile optional fields -------------------------------------------------------

def test_partner_profile_optional_fields_are_saved(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9700000040",
              contact_type="partner", consumer_profile=None,
              partner_profile={
                  "profession_type": "architect",
                  "work_area": "Kozhikode",
                  "sample_given": True,
                  "projects_per_year": 12,
              }).json()
    assert r["partner_profile"]["work_area"] == "Kozhikode"
    assert r["partner_profile"]["sample_given"] is True
    assert r["partner_profile"]["projects_per_year"] == 12


def test_partner_profile_update_changes_only_sent_fields(client, login, lead_source_id):
    headers, _ = login("admin")
    contact = _make(client, headers, lead_source_id("meta"), phone="9700000041",
                    contact_type="partner", consumer_profile=None,
                    partner_profile={"profession_type": "plumber", "work_area": "Feroke"}).json()
    updated = client.put(f"/contacts/{contact['id']}",
                         json={"partner_profile": {"sample_given": True}},
                         headers=headers).json()
    assert updated["partner_profile"]["profession_type"] == "plumber"  # unchanged
    assert updated["partner_profile"]["work_area"] == "Feroke"          # unchanged
    assert updated["partner_profile"]["sample_given"] is True           # updated


# --- dealer profile optional email ---------------------------------------------------------

def test_dealer_profile_email_is_optional(client, login, lead_source_id):
    headers, _ = login("admin")
    without = _make(client, headers, lead_source_id("meta"), phone="9700000050",
                    contact_type="dealer", consumer_profile=None,
                    dealer_profile={"owner_name": "Ravi"}).json()
    assert without["dealer_profile"]["email"] is None

    with_email = _make(client, headers, lead_source_id("meta"), phone="9700000051",
                       contact_type="dealer", consumer_profile=None,
                       dealer_profile={"owner_name": "Kumar", "email": "kumar@example.com"}).json()
    assert with_email["dealer_profile"]["email"] == "kumar@example.com"


def test_dealer_profile_rejects_invalid_email(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9700000052",
              contact_type="dealer", consumer_profile=None,
              dealer_profile={"owner_name": "Bad", "email": "not-an-email"})
    assert r.status_code == 422


# --- consumer profile endpoint --------------------------------------------------------------

def test_consumer_profile_upsert_endpoint_creates_and_updates(client, login, lead_source_id):
    headers, _ = login("admin")
    contact = _make(client, headers, lead_source_id("meta"), phone="9700000060",
                    consumer_profile=None).json()
    assert contact["is_verified"] is False

    # Create profile via dedicated endpoint
    r = client.put(f"/contacts/{contact['id']}/consumer-profile",
                   json={"house_stage": "ready"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["house_stage"] == "ready"

    # Update it
    r2 = client.put(f"/contacts/{contact['id']}/consumer-profile",
                    json={"current_tank_capacity": 500}, headers=headers)
    assert r2.status_code == 200
    assert r2.json()["house_stage"] == "ready"          # unchanged
    assert r2.json()["current_tank_capacity"] == 500    # updated

    # Contact is now verified
    assert client.get(f"/contacts/{contact['id']}", headers=headers).json()["is_verified"] is True


def test_consumer_profile_endpoint_rejects_non_consumer(client, login, lead_source_id):
    headers, _ = login("admin")
    partner = _make(client, headers, lead_source_id("meta"), phone="9700000061",
                    contact_type="partner", consumer_profile=None,
                    partner_profile={"profession_type": "engineer"}).json()
    r = client.put(f"/contacts/{partner['id']}/consumer-profile",
                   json={"house_stage": "ready"}, headers=headers)
    assert r.status_code == 400
    assert "not a consumer" in r.json()["detail"]


def test_consumer_profile_delete_endpoint(client, login, lead_source_id):
    headers, _ = login("admin")
    contact = _make(client, headers, lead_source_id("meta"), phone="9700000062").json()
    assert contact["consumer_profile"] is not None
    r = client.delete(f"/contacts/{contact['id']}/consumer-profile", headers=headers)
    assert r.status_code == 204
    fetched = client.get(f"/contacts/{contact['id']}", headers=headers).json()
    assert fetched["consumer_profile"] is None
    assert fetched["is_verified"] is False
