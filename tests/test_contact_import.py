"""Contact import: analyze, commit, template — backend + edge cases."""
import io
import csv


def _csv(rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue().encode()


def _upload(client, headers, path, content, filename="import.csv", **form):
    return client.post(
        path,
        data=form,
        files={"file": (filename, content, "text/csv")},
        headers=headers,
    )


# --- template -------------------------------------------------------------------------------

def test_template_download_has_correct_headers(client, login):
    headers, _ = login("admin")
    r = client.get("/contacts/import/template", headers=headers)
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    lines = r.content.decode("utf-8-sig").splitlines()
    assert "Name" in lines[0] and "Phone" in lines[0]
    assert len(lines) >= 2  # header + at least one example row


# --- analyze: happy paths -------------------------------------------------------------------

def test_analyze_consumer_all_fields_recognized(client, login, lead_source_id):
    headers, _ = login("admin")
    src = lead_source_id("meta")
    content = _csv([
        ["Name", "Phone", "District", "Address", "Type", "Lead source", "House stage"],
        ["Asha Nair", "9876543210", "Calicut", "MG Road", "consumer", "Meta", "ready"],
    ])
    r = _upload(client, headers, "/contacts/import/analyze", content, default_source_id=src)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["ready"] == 1
    assert body["summary"]["error"] == 0


def test_analyze_partner_row_recognized(client, login, lead_source_id):
    headers, _ = login("admin")
    content = _csv([
        ["Name", "Phone", "District", "Type", "Profession"],
        ["Raju Plumber", "9100001001", "Calicut", "partner", "plumber"],
    ])
    r = _upload(client, headers, "/contacts/import/analyze", content,
                default_source_id=lead_source_id("meta"))
    assert r.status_code == 200
    assert r.json()["summary"]["ready"] == 1


def test_analyze_dealer_row_recognized(client, login, lead_source_id):
    headers, _ = login("admin")
    content = _csv([
        ["Name", "Phone", "District", "Address", "Type", "Owner"],
        ["Kerala Tanks", "9100001002", "Calicut", "MG Road", "dealer", "Ravi"],
    ])
    r = _upload(client, headers, "/contacts/import/analyze", content,
                default_source_id=lead_source_id("meta"))
    assert r.status_code == 200
    assert r.json()["summary"]["ready"] == 1


def test_analyze_nameless_row_is_ready(client, login, lead_source_id):
    """Name is optional — a row with only phone should pass analysis."""
    headers, _ = login("admin")
    content = _csv([
        ["Phone", "District"],
        ["9100001003", "Calicut"],
    ])
    r = _upload(client, headers, "/contacts/import/analyze", content,
                default_source_id=lead_source_id("meta"))
    assert r.status_code == 200
    rows = r.json()["rows"]
    # no error rows
    assert all(row["status"] != "error" for row in rows)


# --- analyze: error rows --------------------------------------------------------------------

def test_analyze_missing_phone_is_error(client, login, lead_source_id):
    headers, _ = login("admin")
    content = _csv([
        ["Name", "Phone"],
        ["No Phone", ""],
    ])
    r = _upload(client, headers, "/contacts/import/analyze", content,
                default_source_id=lead_source_id("meta"))
    rows = r.json()["rows"]
    assert any(row["status"] == "error" and "Phone" in " ".join(row["issues"]) for row in rows)


def test_analyze_invalid_phone_is_error(client, login, lead_source_id):
    headers, _ = login("admin")
    content = _csv([
        ["Name", "Phone"],
        ["Bad Phone", "abcd-xyz"],
    ])
    r = _upload(client, headers, "/contacts/import/analyze", content,
                default_source_id=lead_source_id("meta"))
    rows = r.json()["rows"]
    assert any(row["status"] == "error" for row in rows)


def test_analyze_dealer_without_owner_name_is_error(client, login, lead_source_id):
    headers, _ = login("admin")
    content = _csv([
        ["Name", "Phone", "Type"],
        ["Shop X", "9100001010", "dealer"],
    ])
    r = _upload(client, headers, "/contacts/import/analyze", content,
                default_source_id=lead_source_id("meta"))
    rows = r.json()["rows"]
    assert any(row["status"] == "error" and "owner" in " ".join(row["issues"]).lower() for row in rows)


def test_analyze_duplicate_phone_within_file_is_error(client, login, lead_source_id):
    headers, _ = login("admin")
    content = _csv([
        ["Name", "Phone"],
        ["Alice", "9100001020"],
        ["Alice Again", "9100001020"],
    ])
    r = _upload(client, headers, "/contacts/import/analyze", content,
                default_source_id=lead_source_id("meta"))
    summary = r.json()["summary"]
    assert summary["error"] >= 1


def test_analyze_phone_already_in_crm_is_duplicate(client, login, lead_source_id):
    headers, _ = login("admin")
    # create a contact first
    client.post("/contacts", json={
        "contact_type": "consumer", "phone": "9100001030",
        "lead_source_id": lead_source_id("meta"),
    }, headers=headers)
    content = _csv([
        ["Name", "Phone"],
        ["Same Person", "9100001030"],
    ])
    r = _upload(client, headers, "/contacts/import/analyze", content,
                default_source_id=lead_source_id("meta"))
    assert r.json()["summary"]["duplicate"] == 1


# --- analyze: warning rows ------------------------------------------------------------------

def test_analyze_unknown_type_warns_and_uses_default(client, login, lead_source_id):
    headers, _ = login("admin")
    content = _csv([
        ["Name", "Phone", "Type"],
        ["Mystery", "9100001040", "prospect"],
    ])
    r = _upload(client, headers, "/contacts/import/analyze", content,
                default_source_id=lead_source_id("meta"), default_type="consumer")
    rows = r.json()["rows"]
    assert any(row["status"] == "warning" and "type" in " ".join(row["issues"]).lower() for row in rows)


def test_analyze_unknown_lead_source_warns(client, login, lead_source_id):
    headers, _ = login("admin")
    content = _csv([
        ["Name", "Phone", "Lead source"],
        ["Known Name", "9100001041", "TikTok"],
    ])
    r = _upload(client, headers, "/contacts/import/analyze", content,
                default_source_id=lead_source_id("meta"))
    rows = r.json()["rows"]
    assert any(row["status"] == "warning" and "source" in " ".join(row["issues"]).lower() for row in rows)


def test_analyze_invalid_house_stage_warns(client, login, lead_source_id):
    headers, _ = login("admin")
    content = _csv([
        ["Name", "Phone", "House stage"],
        ["Stage Test", "9100001042", "Under Renovation Done"],
    ])
    r = _upload(client, headers, "/contacts/import/analyze", content,
                default_source_id=lead_source_id("meta"))
    rows = r.json()["rows"]
    assert any(row["status"] == "warning" and "stage" in " ".join(row["issues"]).lower() for row in rows)


def test_analyze_missing_district_warns(client, login, lead_source_id):
    headers, _ = login("admin")
    content = _csv([
        ["Name", "Phone"],
        ["No District", "9100001043"],
    ])
    r = _upload(client, headers, "/contacts/import/analyze", content,
                default_source_id=lead_source_id("meta"))
    rows = r.json()["rows"]
    assert any("district" in " ".join(row["issues"]).lower() for row in rows)


def test_analyze_invalid_alt_phone_warns(client, login, lead_source_id):
    headers, _ = login("admin")
    content = _csv([
        ["Name", "Phone", "Alternate phone"],
        ["Alt Test", "9100001044", "bad-alt"],
    ])
    r = _upload(client, headers, "/contacts/import/analyze", content,
                default_source_id=lead_source_id("meta"))
    rows = r.json()["rows"]
    assert any("alternate" in " ".join(row["issues"]).lower() for row in rows)


def test_analyze_missing_partner_profession_warns_uses_other(client, login, lead_source_id):
    headers, _ = login("admin")
    content = _csv([
        ["Name", "Phone", "Type"],
        ["No Profession", "9100001045", "partner"],
    ])
    r = _upload(client, headers, "/contacts/import/analyze", content,
                default_source_id=lead_source_id("meta"))
    rows = r.json()["rows"]
    assert any("profession" in " ".join(row["issues"]).lower() for row in rows)


# --- analyze: header auto-detect aliases ----------------------------------------------------

def test_analyze_detects_mobile_as_phone_header(client, login, lead_source_id):
    headers, _ = login("admin")
    content = _csv([
        ["Customer", "Mobile", "City"],
        ["Raju", "9100001050", "Kozhikode"],
    ])
    r = _upload(client, headers, "/contacts/import/analyze", content,
                default_source_id=lead_source_id("meta"))
    assert r.json()["summary"]["error"] == 0


# --- file-level errors ----------------------------------------------------------------------

def test_analyze_rejects_wrong_file_type(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _upload(client, headers, "/contacts/import/analyze",
                b"<html>not a csv</html>", filename="import.html",
                default_source_id=lead_source_id("meta"))
    assert r.status_code == 400


def test_analyze_rejects_empty_file(client, login, lead_source_id):
    headers, _ = login("admin")
    r = _upload(client, headers, "/contacts/import/analyze",
                b"", default_source_id=lead_source_id("meta"))
    assert r.status_code == 400


def test_analyze_rejects_non_admin(client, login, lead_source_id):
    headers, _ = login("anu")
    content = _csv([["Name", "Phone"], ["Test", "9100001060"]])
    r = _upload(client, headers, "/contacts/import/analyze", content,
                default_source_id=lead_source_id("meta"))
    assert r.status_code == 403


# --- commit: creates rows and skips errors --------------------------------------------------

def test_commit_creates_ready_and_warning_rows_skips_errors(client, login, lead_source_id):
    headers, _ = login("admin")
    src = lead_source_id("meta")
    content = _csv([
        ["Name", "Phone", "District"],
        ["Good Row", "9100002001", "Calicut"],           # ready
        ["No District", "9100002002", ""],               # warning (missing district)
        ["Bad Phone Row", "not-a-phone", ""],            # error (invalid phone)
    ])
    before = client.get("/contacts", headers=headers).json()["total"]
    r = _upload(client, headers, "/contacts/import/commit", content,
                mapping='{"0":"name","1":"phone","2":"district"}',
                default_source_id=str(src))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 2
    assert body["skipped"] >= 1
    after = client.get("/contacts", headers=headers).json()["total"]
    assert after == before + 2


def test_commit_each_imported_contact_has_new_lead_stage(client, login, lead_source_id):
    headers, _ = login("admin")
    src = lead_source_id("meta")
    content = _csv([
        ["Name", "Phone"],
        ["Stage Check", "9100002010"],
    ])
    _upload(client, headers, "/contacts/import/commit", content,
            mapping='{"0":"name","1":"phone"}',
            default_source_id=str(src))
    contacts = client.get("/contacts", params={"search": "9100002010"}, headers=headers).json()["items"]
    assert len(contacts) == 1
    contact_id = contacts[0]["id"]
    stages = client.get(f"/contacts/{contact_id}/stages", headers=headers).json()
    assert stages[0]["stage_slug"] == "new_lead"


def test_commit_duplicate_skip_does_not_overwrite(client, login, lead_source_id):
    headers, _ = login("admin")
    src = lead_source_id("meta")
    existing = client.post("/contacts", json={
        "contact_type": "consumer", "name": "Original",
        "phone": "9100002020", "lead_source_id": src,
    }, headers=headers).json()

    content = _csv([
        ["Name", "Phone"],
        ["Overwrite Attempt", "9100002020"],
    ])
    r = _upload(client, headers, "/contacts/import/commit", content,
                mapping='{"0":"name","1":"phone"}',
                default_source_id=str(src), duplicates="skip")
    assert r.json()["skipped"] == 1
    assert client.get(f"/contacts/{existing['id']}", headers=headers).json()["name"] == "Original"


def test_commit_update_blank_fills_missing_district(client, login, lead_source_id):
    headers, _ = login("admin")
    src = lead_source_id("meta")
    existing = client.post("/contacts", json={
        "contact_type": "consumer", "phone": "9100002030", "lead_source_id": src,
    }, headers=headers).json()
    assert existing["district"] is None

    content = _csv([
        ["Name", "Phone", "District"],
        ["", "9100002030", "Kozhikode"],   # name blank is fine (optional); phone matches existing
    ])
    r = _upload(client, headers, "/contacts/import/commit", content,
                mapping='{"0":"name","1":"phone","2":"district"}',
                default_source_id=str(src), duplicates="update_blank")
    assert r.json()["updated"] == 1
    updated = client.get(f"/contacts/{existing['id']}", headers=headers).json()
    assert updated["district"] == "Kozhikode"


def test_commit_update_blank_does_not_overwrite_existing_value(client, login, lead_source_id):
    headers, _ = login("admin")
    src = lead_source_id("meta")
    existing = client.post("/contacts", json={
        "contact_type": "consumer", "phone": "9100002031",
        "district": "Calicut", "lead_source_id": src,
    }, headers=headers).json()

    content = _csv([
        ["Name", "Phone", "District"],
        ["", "9100002031", "Thrissur"],
    ])
    _upload(client, headers, "/contacts/import/commit", content,
            mapping='{"0":"name","1":"phone","2":"district"}',
            default_source_id=str(src), duplicates="update_blank")
    assert client.get(f"/contacts/{existing['id']}", headers=headers).json()["district"] == "Calicut"


def test_commit_creates_correct_profiles_per_type(client, login, lead_source_id):
    headers, _ = login("admin")
    src = lead_source_id("meta")
    content = _csv([
        ["Name", "Phone", "Type", "House stage", "Profession", "Owner"],
        ["Consumer A", "9100002040", "consumer", "ready", "", ""],
        ["Partner B", "9100002041", "partner", "", "plumber", ""],
        ["Dealer C", "9100002042", "dealer", "", "", "Ravi"],
    ])
    r = _upload(client, headers, "/contacts/import/commit", content,
                mapping='{"0":"name","1":"phone","2":"contact_type","3":"house_stage","4":"profession_type","5":"owner_name"}',
                default_source_id=str(src))
    assert r.json()["created"] == 3

    items = {c["name"]: c for c in
             client.get("/contacts", headers=headers).json()["items"]
             if c["name"] in ("Consumer A", "Partner B", "Dealer C")}
    assert items["Consumer A"]["house_stage"] == "ready"
    assert items["Partner B"]["profession_type"] == "plumber"
    assert items["Dealer C"]["owner_name"] == "Ravi"


def test_commit_requires_name_and_phone_columns_mapped(client, login, lead_source_id):
    headers, _ = login("admin")
    content = _csv([["Name", "Phone"], ["Test", "9100002050"]])
    r = _upload(client, headers, "/contacts/import/commit", content,
                mapping='{"0":"name"}',  # phone not mapped
                default_source_id=str(lead_source_id("meta")))
    assert r.status_code == 400
    assert "Phone" in r.json()["detail"]


def test_commit_rejects_non_admin(client, login, lead_source_id):
    headers, _ = login("anu")
    content = _csv([["Name", "Phone"], ["Test", "9100002060"]])
    r = _upload(client, headers, "/contacts/import/commit", content,
                mapping='{"0":"name","1":"phone"}',
                default_source_id=str(lead_source_id("meta")))
    assert r.status_code == 403
