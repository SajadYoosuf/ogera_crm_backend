"""Partners move through their own pipeline; consumers keep theirs."""
from database import SessionLocal
from models import PipelineStage


def _contact(client, headers, sources, **overrides):
    body = {
        "contact_type": "consumer", "name": "Arun Kumar", "phone": "9876543210", "district": "Calicut",
        "address": "House 4, MG Road", "lead_source_id": sources("meta"),
        "consumer_profile": {"house_stage": "ready"},
    }
    body.update(overrides)
    response = client.post("/contacts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _partner(client, headers, sources):
    return _contact(client, headers, sources, contact_type="partner", name="Plumber Raju", phone="9222222222",
                    consumer_profile=None, partner_profile={"profession_type": "plumber", "work_area": "Calicut"})


def _stage_id(slug: str, contact_type: str) -> int:
    with SessionLocal() as db:
        row = db.query(PipelineStage).filter(PipelineStage.slug == slug, PipelineStage.contact_type == contact_type).first()
        assert row is not None, f"Stage '{slug}' for type '{contact_type}' not seeded"
        return row.id


def _move(client, headers, admin, contact_id, stage_id):
    return client.post(f"/contacts/{contact_id}/stages", headers=headers,
                       json={"stage_id": stage_id, "changed_by": admin["id"]})


def test_partner_walks_its_own_pipeline(client, login, lead_source_id):
    headers, admin = login("admin")
    partner = _partner(client, headers, lead_source_id)
    for stage in ("connected", "demo_introduction", "aware", "interested", "active", "referral", "inactive"):
        sid = _stage_id(stage, "partner")
        assert _move(client, headers, admin, partner["id"], sid).status_code == 201, stage


def test_partner_cannot_use_consumer_only_stages(client, login, lead_source_id):
    headers, admin = login("admin")
    partner = _partner(client, headers, lead_source_id)
    for stage in ("quotation", "won", "after_sales_referring"):
        sid = _stage_id(stage, "consumer")
        response = _move(client, headers, admin, partner["id"], sid)
        assert response.status_code == 400, stage
        assert "partner pipeline" in response.json()["detail"]


def test_consumer_cannot_use_partner_only_stages(client, login, lead_source_id):
    headers, admin = login("admin")
    consumer = _contact(client, headers, lead_source_id)
    for stage in ("demo_introduction", "active", "inactive"):
        sid = _stage_id(stage, "partner")
        response = _move(client, headers, admin, consumer["id"], sid)
        assert response.status_code == 400, stage
        assert "consumer pipeline" in response.json()["detail"]
    assert _move(client, headers, admin, consumer["id"], _stage_id("first_call", "consumer")).status_code == 201


def test_partner_stages_do_not_appear_on_the_consumer_board(client, login):
    headers, _ = login("anu")
    slugs = [s["slug"] for s in client.get("/pipeline/stages?contact_type=consumer", headers=headers).json()]
    assert not {"demo_introduction", "active", "inactive"} & set(slugs)
