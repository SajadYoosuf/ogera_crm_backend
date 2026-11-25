"""WhatsApp templates: validation rules, /whatsapp/templates, and Meta's status webhook.

Meta is never called: the functions in services.whatsapp_meta_templates are replaced with fakes
that record what they were sent. Rules checked here:
  - a template that breaks Meta's rules is refused with one specific sentence, before Meta is called
  - a draft stays local until it is submitted; submitting stores Meta's id and status
  - a submitted template cannot change name/language and goes back to PENDING when edited
  - templates under review cannot be edited
  - Meta's webhook moves a template to APPROVED / REJECTED and records the reason
"""
import base64
import os

import pytest
from sqlalchemy import select

import services.whatsapp_meta_templates as meta
import services.whatsapp_template_rules as rules
import services.whatsapp_template_service as svc
from database import SessionLocal
from models.whatsapp_template import WhatsAppTemplate
from schemas.whatsapp_template import WhatsAppTemplateInput

CONFIG = {"phone_number_id": "1234567890123", "waba_id": "9876543210987", "access_token": "EAAGsecrettokenvalue1234567890x7Qz"}


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.delenv("WHATSAPP_TEMPLATES_DRY_RUN", raising=False)


def tpl(**over) -> dict:
    base = {"name": "order_update", "language": "en_US", "category": "UTILITY", "body_text": "Hi {{1}}, your order {{2}} is ready.",
            "sample_values": {"body": ["Anu", "A100"], "header": []}}
    return {**base, **over}


def parse(**over) -> WhatsAppTemplateInput:
    return WhatsAppTemplateInput(**tpl(**over))


def connect(client, headers):
    assert client.put("/whatsapp/config", headers=headers, json=CONFIG).status_code == 200


class FakeMeta:
    def __init__(self, monkeypatch):
        self.submitted, self.edited, self.deleted = [], [], []
        self.submit_result = {"id": "555000111", "status": "PENDING", "category": None}
        self.remote: list[dict] = []
        monkeypatch.setattr(meta, "submit_template", self.submit)
        monkeypatch.setattr(meta, "edit_template", self.edit)
        monkeypatch.setattr(meta, "delete_template", self.delete)
        monkeypatch.setattr(meta, "list_templates", lambda creds: self.remote)

    def submit(self, creds, payload):
        if isinstance(self.submit_result, Exception):
            raise self.submit_result
        self.submitted.append((creds, payload))
        return self.submit_result

    def edit(self, creds, meta_id, components, category=None):
        self.edited.append((meta_id, components, category))

    def delete(self, creds, name, meta_id):
        self.deleted.append((name, meta_id))


@pytest.fixture()
def fake(monkeypatch):
    return FakeMeta(monkeypatch)


def make_draft(client, headers, **over) -> dict:
    response = client.post("/whatsapp/templates", headers=headers, json=tpl(**over))
    assert response.status_code == 201, response.text
    return response.json()


# ── rules ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("over,message", [
    ({"name": "Order Update"}, "lowercase letters, numbers and underscores"),
    ({"name": ""}, "name is required"),
    ({"body_text": "  "}, "body is required"),
    ({"body_text": "x" * 1025, "sample_values": None}, "at most 1024"),
    ({"body_text": "Hi {{1}} {{3}}", "sample_values": {"body": ["a", "b"]}}, "no gaps"),
    ({"sample_values": {"body": ["Anu"]}}, "2 variable.s. but 1 sample"),
    ({"sample_values": {"body": ["Anu", " "]}}, "sample value 2 is empty"),
    ({"footer_text": "x" * 61}, "footer can be at most 60"),
    ({"footer_text": "Hi {{1}}"}, "footer cannot contain variables"),
    ({"header_type": "text", "header_content": "A {{1}} {{2}}"}, "only one variable"),
    ({"header_type": "text", "header_content": ""}, "header text is required"),
    ({"header_type": "image"}, "needs a sample file link"),
    ({"header_type": "image", "header_media_url": "ftp://x"}, "https://"),
    ({"buttons": [{"type": "URL", "text": "Open", "url": ""}]}, "Button 1 needs a URL"),
    ({"buttons": [{"type": "URL", "text": "Open", "url": "https://x.com/{{1}}"}]}, "needs an example link"),
    ({"buttons": [{"type": "QUICK_REPLY", "text": "Yes"}, {"type": "PHONE_NUMBER", "text": "Call", "phone_number": "+9100"},
                  {"type": "QUICK_REPLY", "text": "No"}]}, "grouped together"),
    ({"buttons": [{"type": "PHONE_NUMBER", "text": "Call", "phone_number": ""}]}, "needs a phone number"),
    ({"buttons": [{"type": "COPY_CODE", "text": "Copy"}]}, "needs an example code"),
    ({"buttons": [{"type": "QUICK_REPLY", "text": "x" * 26}]}, "at most 25"),
    ({"buttons": [{"type": "URL", "text": "A", "url": "https://a.com"}] * 3}, "at most 2 URL"),
])
def test_rules_refuse_with_a_specific_sentence(over, message):
    with pytest.raises(rules.TemplateRuleError, match=message):
        rules.validate_template(parse(**over))


def test_valid_template_with_every_part_passes_and_builds_metas_components_in_order():
    t = parse(
        header_type="text", header_content="Order {{1}}", footer_text="Ventino",
        sample_values={"body": ["Anu", "A100"], "header": ["A100"]},
        buttons=[{"type": "QUICK_REPLY", "text": "Thanks"},
                 {"type": "URL", "text": "Track", "url": "https://x.com/t/{{1}}", "example": "https://x.com/t/9"},
                 {"type": "PHONE_NUMBER", "text": "Call", "phone_number": "+917994437444"}],
    )
    assert rules.validate_template(t) == (2, 1)
    components = rules.build_meta_components(t)
    assert [c["type"] for c in components] == ["HEADER", "BODY", "FOOTER", "BUTTONS"]
    assert components[0]["example"] == {"header_text": ["A100"]}
    assert components[1]["example"] == {"body_text": [["Anu", "A100"]]}
    assert components[3]["buttons"][1] == {"type": "URL", "text": "Track", "url": "https://x.com/t/{{1}}", "example": ["https://x.com/t/9"]}


def test_media_header_uses_the_handle_before_the_url():
    t = parse(header_type="image", header_media_url="https://x.com/a.jpg", header_handle="4::abc", sample_values={"body": ["a", "b"]})
    rules.validate_template(t)
    assert rules.build_meta_components(t)[0] == {"type": "HEADER", "format": "IMAGE", "example": {"header_handle": ["4::abc"]}}


def test_parse_meta_components_round_trips_what_we_build():
    t = parse(header_type="text", header_content="Order {{1}}", footer_text="Ventino",
              sample_values={"body": ["Anu", "A100"], "header": ["A100"]},
              buttons=[{"type": "QUICK_REPLY", "text": "Thanks"}])
    parsed = rules.parse_meta_components(rules.build_meta_components(t))
    assert parsed["body_text"] == t.body_text and parsed["footer_text"] == "Ventino"
    assert parsed["header_type"] == "text" and parsed["header_content"] == "Order {{1}}"
    assert parsed["sample_values"] == {"body": ["Anu", "A100"], "header": ["A100"]}
    assert parsed["buttons"][0]["type"] == "QUICK_REPLY"


def test_status_words():
    assert rules.normalize_status("pending_review") == "PENDING"
    assert rules.normalize_status("whatever") == "PENDING"
    assert rules.normalize_status("approved") == "APPROVED"
    assert rules.status_from_webhook_event("REINSTATED") == "APPROVED"
    assert rules.status_from_webhook_event("FLAGGED") is None
    assert rules.normalize_quality({"score": "green"}) == "GREEN"
    assert rules.normalize_quality("UNKNOWN") is None


# ── routes ─────────────────────────────────────────────────────────────────────

def test_create_saves_a_draft_without_calling_meta(client, login, fake):
    headers, _ = login("admin")
    created = make_draft(client, headers)
    assert created["status"] == "DRAFT" and created["meta_template_id"] is None
    assert created["body_variable_count"] == 2 and created["can_edit"] and created["can_submit"]
    assert fake.submitted == []
    assert client.get(f"/whatsapp/templates/{created['id']}", headers=headers).json()["name"] == "order_update"


def test_invalid_template_is_refused_with_the_rule_sentence(client, login):
    headers, _ = login("admin")
    response = client.post("/whatsapp/templates", headers=headers, json=tpl(name="Bad Name"))
    assert response.status_code == 422
    assert response.json()["detail"] == "The name can use only lowercase letters, numbers and underscores."


def test_same_name_and_language_conflicts_but_another_language_is_fine(client, login):
    headers, _ = login("admin")
    make_draft(client, headers)
    assert client.post("/whatsapp/templates", headers=headers, json=tpl()).status_code == 409
    assert client.post("/whatsapp/templates", headers=headers, json=tpl(language="ml")).status_code == 201


def test_list_filters_by_status_and_name(client, login, fake):
    headers, _ = login("admin")
    connect(client, headers)
    a = make_draft(client, headers, name="alpha_one")
    make_draft(client, headers, name="beta_two")
    client.post(f"/whatsapp/templates/{a['id']}/submit", headers=headers)

    assert client.get("/whatsapp/templates", headers=headers).json()["total"] == 2
    assert [t["name"] for t in client.get("/whatsapp/templates?status=draft", headers=headers).json()["items"]] == ["beta_two"]
    assert [t["name"] for t in client.get("/whatsapp/templates?search=ALPHA", headers=headers).json()["items"]] == ["alpha_one"]


def test_submit_sends_metas_payload_and_stores_its_id_and_status(client, login, fake):
    headers, _ = login("admin")
    connect(client, headers)
    draft = make_draft(client, headers)

    submitted = client.post(f"/whatsapp/templates/{draft['id']}/submit", headers=headers).json()
    assert submitted["meta_template_id"] == "555000111" and submitted["status"] == "PENDING"
    assert submitted["can_submit"] is False and submitted["can_edit"] is False
    creds, payload = fake.submitted[0]
    assert creds.waba_id == CONFIG["waba_id"] and creds.access_token == CONFIG["access_token"]
    assert payload["name"] == "order_update" and payload["category"] == "UTILITY" and payload["language"] == "en_US"
    assert client.post(f"/whatsapp/templates/{draft['id']}/submit", headers=headers).status_code == 409


def test_submit_needs_whatsapp_connected_first(client, login, fake):
    headers, _ = login("admin")
    draft = make_draft(client, headers)
    response = client.post(f"/whatsapp/templates/{draft['id']}/submit", headers=headers)
    assert response.status_code == 409 and "Connect WhatsApp" in response.json()["detail"]


def test_metas_refusal_is_shown_and_kept_on_the_draft(client, login, fake):
    headers, _ = login("admin")
    connect(client, headers)
    draft = make_draft(client, headers)
    fake.submit_result = meta.MetaApiError("Template name already exists in this language", 400)

    response = client.post(f"/whatsapp/templates/{draft['id']}/submit", headers=headers)
    assert response.status_code == 400 and "already exists" in response.json()["detail"]
    row = client.get(f"/whatsapp/templates/{draft['id']}", headers=headers).json()
    assert row["status"] == "DRAFT" and row["meta_template_id"] is None and "already exists" in row["submission_error"]


def test_authentication_templates_are_not_sent(client, login, fake):
    headers, _ = login("admin")
    connect(client, headers)
    draft = make_draft(client, headers, category="AUTHENTICATION")
    assert client.post(f"/whatsapp/templates/{draft['id']}/submit", headers=headers).status_code == 422
    assert fake.submitted == []


def test_editing_a_draft_is_local_and_may_rename(client, login, fake):
    headers, _ = login("admin")
    draft = make_draft(client, headers)
    response = client.put(f"/whatsapp/templates/{draft['id']}", headers=headers, json=tpl(name="renamed", body_text="Hello {{1}}", sample_values={"body": ["Anu"]}))
    assert response.status_code == 200 and response.json()["name"] == "renamed"
    assert fake.edited == []


def test_editing_a_submitted_template_goes_back_to_meta_and_to_pending(client, login, fake):
    headers, _ = login("admin")
    connect(client, headers)
    draft = make_draft(client, headers)
    client.post(f"/whatsapp/templates/{draft['id']}/submit", headers=headers)
    with SessionLocal() as db:  # Meta approved it
        row = db.get(WhatsAppTemplate, draft["id"])
        row.status = "APPROVED"
        db.commit()

    body = tpl(body_text="Hi {{1}}, thanks!", sample_values={"body": ["Anu"]})
    response = client.put(f"/whatsapp/templates/{draft['id']}", headers=headers, json=body)
    assert response.status_code == 200 and response.json()["status"] == "PENDING"
    meta_id, components, category = fake.edited[0]
    assert meta_id == "555000111" and components[0]["text"] == "Hi {{1}}, thanks!" and category is None

    renamed = client.put(f"/whatsapp/templates/{draft['id']}", headers=headers, json={**body, "name": "other"})
    assert renamed.status_code == 409  # still PENDING after the edit, so it is locked anyway


def test_a_submitted_template_cannot_change_name_or_language(client, login, fake):
    headers, _ = login("admin")
    connect(client, headers)
    draft = make_draft(client, headers)
    client.post(f"/whatsapp/templates/{draft['id']}/submit", headers=headers)
    with SessionLocal() as db:
        db.get(WhatsAppTemplate, draft["id"]).status = "REJECTED"
        db.commit()
    response = client.put(f"/whatsapp/templates/{draft['id']}", headers=headers, json=tpl(name="other"))
    assert response.status_code == 422 and "cannot be changed" in response.json()["detail"]
    assert fake.edited == []


def test_a_template_under_review_cannot_be_edited(client, login, fake):
    headers, _ = login("admin")
    connect(client, headers)
    draft = make_draft(client, headers)
    client.post(f"/whatsapp/templates/{draft['id']}/submit", headers=headers)  # PENDING
    response = client.put(f"/whatsapp/templates/{draft['id']}", headers=headers, json=tpl())
    assert response.status_code == 409 and "pending" in response.json()["detail"]


def test_delete_removes_it_on_meta_too(client, login, fake):
    headers, _ = login("admin")
    connect(client, headers)
    submitted = make_draft(client, headers)
    client.post(f"/whatsapp/templates/{submitted['id']}/submit", headers=headers)
    local_only = make_draft(client, headers, name="just_a_draft")

    assert client.delete(f"/whatsapp/templates/{submitted['id']}", headers=headers).status_code == 204
    assert client.delete(f"/whatsapp/templates/{local_only['id']}", headers=headers).status_code == 204
    assert fake.deleted == [("order_update", "555000111")]
    assert client.get("/whatsapp/templates", headers=headers).json()["total"] == 0
    assert client.get(f"/whatsapp/templates/{submitted['id']}", headers=headers).status_code == 404


def test_sync_creates_and_updates_from_meta_and_leaves_unknown_drafts_alone(client, login, fake):
    headers, _ = login("admin")
    connect(client, headers)
    make_draft(client, headers, name="only_local")
    existing = make_draft(client, headers, name="from_meta")
    fake.remote = [
        {"id": "900", "name": "from_meta", "language": "en_US", "status": "APPROVED", "category": "MARKETING",
         "quality_score": {"score": "GREEN"},
         "components": [{"type": "BODY", "text": "Hello {{1}}", "example": {"body_text": [["Anu"]]}}, {"type": "FOOTER", "text": "Ventino"}]},
        {"id": "901", "name": "made_in_meta", "language": "ml", "status": "REJECTED", "category": "UTILITY",
         "components": [{"type": "BODY", "text": "Hi"}]},
    ]
    result = client.post("/whatsapp/templates/sync", headers=headers).json()
    assert result == {"fetched": 2, "created": 1, "updated": 1}

    updated = client.get(f"/whatsapp/templates/{existing['id']}", headers=headers).json()
    assert updated["status"] == "APPROVED" and updated["meta_template_id"] == "900" and updated["quality_score"] == "GREEN"
    assert updated["body_text"] == "Hello {{1}}" and updated["footer_text"] == "Ventino"
    names = {t["name"]: t for t in client.get("/whatsapp/templates", headers=headers).json()["items"]}
    assert set(names) == {"only_local", "from_meta", "made_in_meta"}
    assert names["only_local"]["status"] == "DRAFT"
    assert names["made_in_meta"]["status"] == "REJECTED" and names["made_in_meta"]["language"] == "ml"


def test_dry_run_skips_meta_entirely(client, login, fake, monkeypatch):
    monkeypatch.setenv("WHATSAPP_TEMPLATES_DRY_RUN", "1")
    headers, _ = login("admin")  # no WhatsApp connection saved
    draft = make_draft(client, headers)
    submitted = client.post(f"/whatsapp/templates/{draft['id']}/submit", headers=headers).json()
    assert submitted["status"] == "PENDING" and submitted["meta_template_id"].startswith("dry-run-")
    assert fake.submitted == []
    assert client.delete(f"/whatsapp/templates/{draft['id']}", headers=headers).status_code == 204
    assert fake.deleted == []


def test_permissions(client, login):
    sales, _ = login("anu")
    assert client.get("/whatsapp/templates", headers=sales).status_code == 403
    assert client.post("/whatsapp/templates", headers=sales, json=tpl()).status_code == 403
    manager, _ = login("manager")
    assert client.get("/whatsapp/templates", headers=manager).status_code == 200
    assert client.get("/whatsapp/templates").status_code in (401, 403)


# ── Meta webhook ───────────────────────────────────────────────────────────────

def status_event(**over):
    return {"event": "APPROVED", "message_template_id": "555000111", "message_template_name": "order_update",
            "message_template_language": "en_US", **over}


def test_webhook_approves_and_rejects_with_a_reason(client, login, fake):
    headers, _ = login("admin")
    connect(client, headers)
    draft = make_draft(client, headers)
    client.post(f"/whatsapp/templates/{draft['id']}/submit", headers=headers)

    with SessionLocal() as db:
        assert svc.apply_webhook_change(db, "message_template_status_update", status_event(event="REJECTED", reason="INVALID_FORMAT"))
    row = client.get(f"/whatsapp/templates/{draft['id']}", headers=headers).json()
    assert row["status"] == "REJECTED" and row["rejection_reason"] == "INVALID_FORMAT" and row["can_edit"] is True

    with SessionLocal() as db:
        svc.apply_webhook_change(db, "message_template_status_update", status_event(event="APPROVED", reason="NONE"))
    row = client.get(f"/whatsapp/templates/{draft['id']}", headers=headers).json()
    assert row["status"] == "APPROVED" and row["rejection_reason"] is None


def test_webhook_records_quality_and_ignores_unknown_events(client, login, fake):
    headers, _ = login("admin")
    connect(client, headers)
    draft = make_draft(client, headers)
    client.post(f"/whatsapp/templates/{draft['id']}/submit", headers=headers)

    with SessionLocal() as db:
        assert svc.apply_webhook_change(db, "message_template_quality_update", {"message_template_id": "555000111", "new_quality_score": "YELLOW"})
        assert svc.apply_webhook_change(db, "message_template_status_update", status_event(event="SOMETHING_NEW")) is False
        assert svc.apply_webhook_change(db, "message_template_components_update", status_event()) is False
    assert client.get(f"/whatsapp/templates/{draft['id']}", headers=headers).json()["quality_score"] == "YELLOW"


def test_webhook_for_a_template_made_in_meta_creates_a_stub(client, login):
    headers, _ = login("admin")
    with SessionLocal() as db:
        assert svc.apply_webhook_change(db, "message_template_status_update",
                                        status_event(message_template_id="777", message_template_name="from_business_manager"))
        row = db.scalar(select(WhatsAppTemplate).where(WhatsAppTemplate.name == "from_business_manager"))
        assert row.status == "APPROVED" and row.meta_template_id == "777" and row.body_text == ""
