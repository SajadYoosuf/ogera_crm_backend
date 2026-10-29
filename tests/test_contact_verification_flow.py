"""
High-priority contact verification flow tests.

Covers:
  - Verification gate: contact cannot advance past new_lead until verified
  - Verification fields per type: consumer, partner, dealer
  - Name required for all types (at verification, not at creation)
  - GPS (lat/lng) required from mobile for field_visit source, forbidden for all others
  - Partner profile optional at creation, required at verification
  - Interested products work for both consumer and partner
  - Referral tracking (referred_by_id)
  - verified=true/false list filter works correctly

WHAT IS A VERIFIED CONTACT?
    A contact whose missing_fields list is empty. Each type has its own required fields:
        All types:  phone, name, district, address, lead_source
        Consumer:   + house_stage
        Partner:    + profession_type, work_area
        Dealer:     + owner_name
        Field visit: + latitude, longitude (mobile only at creation)
"""

MOBILE = {"X-Client-Type": "mobile"}


def _make(client, headers, source_id, **overrides):
    """Default body creates a fully-verified consumer contact."""
    body = {
        "contact_type": "consumer",
        "name": "Arun Kumar",
        "phone": "9600000000",
        "district": "Calicut",
        "address": "MG Road",
        "lead_source_id": source_id,
        "consumer_profile": {"house_stage": "ready"},
    }
    body.update(overrides)
    return client.post("/contacts", json=body, headers=headers)


# ── Verification gate (stage transitions) ─────────────────────────────────────

def test_verified_contact_can_advance_past_new_lead(client, login, lead_source_id, stage_id_for):
    """A fully verified contact can be moved to the next pipeline stage."""
    headers, emp = login("admin")
    contact = _make(client, headers, lead_source_id("meta"), phone="9600000010").json()
    assert contact["is_verified"] is True
    next_stage = stage_id_for("aware", "consumer")
    r = client.post(f"/contacts/{contact['id']}/stages",
                    json={"stage_id": next_stage, "changed_by": emp["id"]},
                    headers=headers)
    assert r.status_code == 201, r.text


def test_unverified_contact_cannot_advance_past_new_lead(client, login, lead_source_id, stage_id_for):
    """Missing fields block the stage transition with a 409 and the list of gaps."""
    headers, emp = login("admin")
    contact = _make(client, headers, lead_source_id("meta"), phone="9600000011",
                    name=None, district=None, consumer_profile=None).json()
    assert contact["is_verified"] is False
    next_stage = stage_id_for("aware", "consumer")
    r = client.post(f"/contacts/{contact['id']}/stages",
                    json={"stage_id": next_stage, "changed_by": emp["id"]},
                    headers=headers)
    assert r.status_code == 409
    assert "missing_fields" in r.json()["detail"]
    assert "name" in r.json()["detail"]["missing_fields"]


def test_filling_missing_fields_then_allows_stage_advance(client, login, lead_source_id, stage_id_for):
    """After filling all gaps the stage transition succeeds."""
    headers, emp = login("admin")
    contact = _make(client, headers, lead_source_id("meta"), phone="9600000012",
                    name=None, consumer_profile=None).json()
    assert contact["is_verified"] is False

    # Fill the missing fields
    r = client.put(f"/contacts/{contact['id']}",
                   json={"name": "Priya Nair", "consumer_profile": {"house_stage": "ready"}},
                   headers=headers)
    assert r.status_code == 200
    assert r.json()["is_verified"] is True

    next_stage = stage_id_for("aware", "consumer")
    r2 = client.post(f"/contacts/{contact['id']}/stages",
                     json={"stage_id": next_stage, "changed_by": emp["id"]},
                     headers=headers)
    assert r2.status_code == 201, r2.text


# ── Name required for all types ───────────────────────────────────────────────

def test_name_missing_blocks_consumer_verification(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9600000020", name=None).json()
    assert r["is_verified"] is False
    assert "name" in r["missing_fields"]


def test_name_missing_blocks_partner_verification(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9600000021",
              name=None, contact_type="partner", consumer_profile=None,
              partner_profile={"profession_type": "plumber", "work_area": "Calicut"}).json()
    assert r["is_verified"] is False
    assert "name" in r["missing_fields"]


def test_name_missing_blocks_dealer_verification(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9600000022",
              name=None, contact_type="dealer", consumer_profile=None,
              dealer_profile={"owner_name": "Ravi Stores"}).json()
    assert r["is_verified"] is False
    assert "name" in r["missing_fields"]


def test_adding_name_via_update_verifies_contact(client, login, lead_source_id):
    """A contact with name=None becomes verified once name is patched in."""
    headers, _ = login("admin")
    created = _make(client, headers, lead_source_id("meta"), phone="9600000023", name=None).json()
    assert "name" in created["missing_fields"]

    updated = client.put(f"/contacts/{created['id']}", json={"name": "Kavya M"}, headers=headers).json()
    assert updated["is_verified"] is True
    assert updated["missing_fields"] == []


# ── Consumer-specific verification ────────────────────────────────────────────

def test_consumer_verified_with_all_fields(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9600000030").json()
    assert r["is_verified"] is True
    assert r["missing_fields"] == []


def test_consumer_unverified_without_house_stage(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9600000031",
              consumer_profile=None).json()
    assert r["is_verified"] is False
    assert "house_stage" in r["missing_fields"]


def test_consumer_missing_fields_excludes_optional_data(client, login, lead_source_id):
    """phone_alt, notes, referred_by_id are never in missing_fields."""
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9600000032",
              district=None, consumer_profile=None).json()
    missing = set(r["missing_fields"])
    assert "phone_alt" not in missing
    assert "notes" not in missing
    assert "referred_by_id" not in missing
    assert "district" in missing
    assert "house_stage" in missing


# ── Partner-specific verification ─────────────────────────────────────────────

def test_partner_can_be_created_without_profile(client, login, lead_source_id):
    """partner_profile is not required at creation — the contact is just unverified."""
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9600000040",
              contact_type="partner", consumer_profile=None)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["partner_profile"] is None
    assert "profession_type" in body["missing_fields"]
    assert "work_area" in body["missing_fields"]


def test_partner_verified_when_profile_added_via_update(client, login, lead_source_id):
    """Filling profession_type and work_area via PUT makes the partner verified."""
    headers, _ = login("admin")
    created = _make(client, headers, lead_source_id("meta"), phone="9600000041",
                    contact_type="partner", consumer_profile=None).json()
    assert created["is_verified"] is False

    updated = client.put(f"/contacts/{created['id']}",
                         json={"partner_profile": {"profession_type": "plumber", "work_area": "Feroke"}},
                         headers=headers).json()
    assert updated["is_verified"] is True
    assert updated["missing_fields"] == []


def test_partner_with_profession_but_no_work_area_is_unverified(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9600000042",
              contact_type="partner", consumer_profile=None,
              partner_profile={"profession_type": "plumber"}).json()
    assert r["is_verified"] is False
    assert "work_area" in r["missing_fields"]
    assert "profession_type" not in r["missing_fields"]


def test_partner_verified_flag_appears_in_list(client, login, lead_source_id):
    """verified=true filter returns the partner once all fields are set."""
    headers, _ = login("admin")
    contact = _make(client, headers, lead_source_id("meta"), phone="9600000043",
                    contact_type="partner", consumer_profile=None,
                    partner_profile={"profession_type": "plumber", "work_area": "Calicut"}).json()

    verified_list = client.get("/contacts?verified=true&contact_type=partner", headers=headers).json()
    ids = [c["id"] for c in verified_list["items"]]
    assert contact["id"] in ids

    unverified_list = client.get("/contacts?verified=false&contact_type=partner", headers=headers).json()
    ids_unv = [c["id"] for c in unverified_list["items"]]
    assert contact["id"] not in ids_unv


# ── Dealer-specific verification ──────────────────────────────────────────────

def test_dealer_verified_with_all_required_fields(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9600000050",
              name="Kerala Tanks", contact_type="dealer", consumer_profile=None,
              dealer_profile={"owner_name": "Ravi"}).json()
    assert r["is_verified"] is True
    assert r["missing_fields"] == []


def test_dealer_without_profile_is_rejected_at_creation(client, login, lead_source_id):
    """Dealer requires dealer_profile at create time (unlike partner)."""
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9600000051",
              contact_type="dealer", consumer_profile=None)
    assert r.status_code == 400
    assert "owner_name" in r.json()["detail"]


# ── GPS / field_visit source ──────────────────────────────────────────────────

def test_field_visit_mobile_requires_gps_on_create(client, login, lead_source_id):
    """Mobile + field_visit source → latitude and longitude are required."""
    headers, _ = login("admin")
    r = _make(client, {**headers, **MOBILE}, lead_source_id("field_visit"),
              phone="9600000060", latitude=None, longitude=None)
    assert r.status_code == 400
    assert "latitude and longitude" in r.json()["detail"]


def test_field_visit_mobile_with_gps_succeeds(client, login, lead_source_id):
    """Mobile + field_visit source + lat/lng → 201."""
    headers, _ = login("admin")
    r = _make(client, {**headers, **MOBILE}, lead_source_id("field_visit"),
              phone="9600000061", latitude=11.2588, longitude=75.7804)
    assert r.status_code == 201, r.text
    assert r.json()["latitude"] == 11.2588
    assert r.json()["longitude"] == 75.7804


def test_field_visit_web_does_not_require_gps(client, login, lead_source_id):
    """Web (no X-Client-Type header) can create a field_visit contact without GPS."""
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("field_visit"), phone="9600000062")
    assert r.status_code == 201, r.text


def test_non_field_visit_source_rejects_gps(client, login, lead_source_id):
    """GPS coordinates are only allowed for field_visit source."""
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"),
              phone="9600000063", latitude=11.2588, longitude=75.7804)
    assert r.status_code == 400
    assert "field visit" in r.json()["detail"].lower()


def test_field_visit_contact_unverified_without_gps(client, login, lead_source_id):
    """A field_visit contact created via web (no GPS) shows location in missing_fields."""
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("field_visit"),
              phone="9600000064").json()
    assert r["is_verified"] is False
    assert "location" in r["missing_fields"]


def test_field_visit_contact_verified_with_gps(client, login, lead_source_id):
    """Field visit contact with GPS coordinates is verified (when all other fields also set)."""
    headers, _ = login("admin")
    r = _make(client, {**headers, **MOBILE}, lead_source_id("field_visit"),
              phone="9600000065", latitude=11.2588, longitude=75.7804).json()
    assert r["is_verified"] is True
    assert r["missing_fields"] == []


# ── Interested products ────────────────────────────────────────────────────────

_product_counter = 0

def _product_id(client, headers):
    """Create a product and return its id. Products require a unique slug."""
    global _product_counter
    _product_counter += 1
    r = client.post("/products",
                    json={"name": f"Tank {_product_counter}", "slug": f"tank-{_product_counter}"},
                    headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_consumer_can_link_interested_products(client, login, lead_source_id):
    headers, _ = login("admin")
    pid = _product_id(client, headers)
    contact = _make(client, headers, lead_source_id("meta"), phone="9600000070",
                    interested_product_ids=[pid]).json()
    assert len(contact["interested_products"]) == 1
    assert contact["interested_products"][0]["id"] == pid


def test_partner_can_link_interested_products(client, login, lead_source_id):
    """Partners can also link products — e.g. products they recommend or purchase."""
    headers, _ = login("admin")
    pid = _product_id(client, headers)
    contact = _make(client, headers, lead_source_id("meta"), phone="9600000071",
                    contact_type="partner", consumer_profile=None,
                    partner_profile={"profession_type": "plumber", "work_area": "Calicut"},
                    interested_product_ids=[pid]).json()
    assert len(contact["interested_products"]) == 1
    assert contact["interested_products"][0]["id"] == pid


def test_invalid_product_id_is_rejected(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _make(client, headers, lead_source_id("meta"), phone="9600000072",
              interested_product_ids=[99999])
    assert r.status_code == 400
    assert "product" in r.json()["detail"].lower()


def test_products_can_be_replaced_on_update(client, login, lead_source_id):
    headers, _ = login("admin")
    pid1 = _product_id(client, headers)
    pid2 = _product_id(client, headers)
    contact = _make(client, headers, lead_source_id("meta"), phone="9600000073",
                    interested_product_ids=[pid1]).json()
    updated = client.put(f"/contacts/{contact['id']}",
                         json={"interested_product_ids": [pid2]},
                         headers=headers).json()
    ids = [p["id"] for p in updated["interested_products"]]
    assert pid2 in ids
    assert pid1 not in ids


def test_sending_empty_product_list_clears_products(client, login, lead_source_id):
    headers, _ = login("admin")
    pid = _product_id(client, headers)
    contact = _make(client, headers, lead_source_id("meta"), phone="9600000074",
                    interested_product_ids=[pid]).json()
    assert len(contact["interested_products"]) == 1

    updated = client.put(f"/contacts/{contact['id']}",
                         json={"interested_product_ids": []},
                         headers=headers).json()
    assert updated["interested_products"] == []


def test_completed_stage_creates_linked_sale_without_house_stage(client, login, lead_source_id, stage_id_for):
    headers, employee = login("admin")
    pid = _product_id(client, headers)
    contact = _make(client, headers, lead_source_id("meta"), phone="9600000075",
                    consumer_profile=None, interested_product_ids=[pid]).json()

    assert contact["missing_fields"] == ["house_stage"]
    response = client.post(
        f"/contacts/{contact['id']}/stages",
        json={"stage_id": stage_id_for("won", "consumer"), "changed_by": employee["id"]},
        headers=headers,
    )
    assert response.status_code == 201, response.text

    sales = client.get("/sales", headers=headers).json()
    assert sales["total"] == 1
    sale = sales["items"][0]
    assert sale["contact_id"] == contact["id"]
    assert sale["created_by_employee_id"] == employee["id"]
    assert sale["purchased_product"] == f"Tank {_product_counter}"


def test_completed_stage_does_not_duplicate_sale(client, login, lead_source_id, stage_id_for):
    headers, employee = login("admin")
    pid = _product_id(client, headers)
    contact = _make(client, headers, lead_source_id("meta"), phone="9600000076",
                    consumer_profile=None, interested_product_ids=[pid]).json()
    won = stage_id_for("won", "consumer")
    after_sales = stage_id_for("after_sales_referring", "consumer")

    for stage_id in (won, after_sales):
        response = client.post(
            f"/contacts/{contact['id']}/stages",
            json={"stage_id": stage_id, "changed_by": employee["id"]},
            headers=headers,
        )
        assert response.status_code == 201, response.text

    assert client.get("/sales", headers=headers).json()["total"] == 1


def test_multiple_interested_products_are_rejected(client, login, lead_source_id):
    headers, _ = login("admin")
    pid1 = _product_id(client, headers)
    pid2 = _product_id(client, headers)
    response = _make(client, headers, lead_source_id("meta"), phone="9600000077",
                     interested_product_ids=[pid1, pid2])
    assert response.status_code == 422
    assert "one interested product" in response.text.lower()


# ── Referral tracking ─────────────────────────────────────────────────────────

def test_referral_is_recorded_and_counted(client, login, lead_source_id):
    """referred_by_id links two contacts; referrals_count on the referrer increases."""
    headers, _ = login("admin")
    referrer = _make(client, headers, lead_source_id("meta"), phone="9600000080").json()
    referee = _make(client, headers, lead_source_id("meta"), phone="9600000081",
                    referred_by_id=referrer["id"]).json()

    assert referee["referred_by"]["id"] == referrer["id"]

    referrer_detail = client.get(f"/contacts/{referrer['id']}", headers=headers).json()
    assert referrer_detail["referrals_count"] == 1


def test_self_referral_is_rejected(client, login, lead_source_id):
    headers, _ = login("admin")
    contact = _make(client, headers, lead_source_id("meta"), phone="9600000082").json()
    r = client.put(f"/contacts/{contact['id']}",
                   json={"referred_by_id": contact["id"]},
                   headers=headers)
    assert r.status_code == 400
    assert "cannot refer itself" in r.json()["detail"]


def test_referral_to_deleted_contact_is_rejected(client, login, lead_source_id):
    headers, _ = login("admin")
    referrer = _make(client, headers, lead_source_id("meta"), phone="9600000083").json()
    client.delete(f"/contacts/{referrer['id']}", headers=headers)
    r = _make(client, headers, lead_source_id("meta"), phone="9600000084",
              referred_by_id=referrer["id"])
    assert r.status_code == 400


# ── verified filter in the contact list ──────────────────────────────────────

def test_verified_filter_true_returns_only_verified(client, login, lead_source_id):
    headers, _ = login("admin")
    _make(client, headers, lead_source_id("meta"), phone="9600000090")               # verified
    _make(client, headers, lead_source_id("meta"), phone="9600000091", name=None)     # unverified

    r = client.get("/contacts?verified=true", headers=headers).json()
    for item in r["items"]:
        assert item["is_verified"] is True


def test_verified_filter_false_returns_only_unverified(client, login, lead_source_id):
    headers, _ = login("admin")
    _make(client, headers, lead_source_id("meta"), phone="9600000092")               # verified
    _make(client, headers, lead_source_id("meta"), phone="9600000093", name=None)     # unverified

    r = client.get("/contacts?verified=false", headers=headers).json()
    for item in r["items"]:
        assert item["is_verified"] is False
