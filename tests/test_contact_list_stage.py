"""GET /contacts carries each row's current pipeline stage, so the web board can place cards in columns."""
import datetime

import pytest

from database import SessionLocal
from models import LeadStage, PipelineStage


def _create(client, headers, lead_source_id, contact_type, phone) -> int:
    body = {
        "contact_type": contact_type, "name": f"{contact_type} {phone}", "phone": phone,
        "district": "Malappuram", "address": "Main Road", "lead_source_id": lead_source_id,
    }
    response = client.post("/contacts", headers=headers, json=body)
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def _rows(client, headers, contact_type) -> dict[int, dict]:
    response = client.get(f"/contacts?contact_type={contact_type}&limit=100", headers=headers)
    assert response.status_code == 200, response.text
    return {row["id"]: row for row in response.json()["items"]}


def _log_stage(contact_id, stage_id, changed_by, changed_at):
    with SessionLocal() as db:
        db.add(LeadStage(contact_id=contact_id, stage_id=stage_id, changed_by=changed_by, changed_at=changed_at))
        db.commit()


@pytest.mark.parametrize("contact_type", ["consumer", "partner", "dealer"])
def test_list_row_has_the_newest_stage(client, login, lead_source_id, contact_type):
    headers, admin = login("admin")
    with SessionLocal() as db:
        first = PipelineStage(name="Test First", slug="test_first", contact_type=contact_type, order=50)
        second = PipelineStage(name="Test Second", slug="test_second", contact_type=contact_type, order=51)
        db.add_all([first, second])
        db.commit()
        first_id, second_id = first.id, second.id

    moved = _create(client, headers, lead_source_id("website"), contact_type, "9100000001")
    other = _create(client, headers, lead_source_id("website"), contact_type, "9100000002")
    now = datetime.datetime(2030, 1, 1, 12, 0)
    # The newest row is inserted first, so the result must come from changed_at and not from the row id.
    _log_stage(moved, second_id, admin["id"], now)
    _log_stage(moved, first_id, admin["id"], now - datetime.timedelta(days=1))
    _log_stage(other, first_id, admin["id"], now)

    rows = _rows(client, headers, contact_type)

    assert rows[moved]["lead_stage"] == {
        "stage_id": second_id, "stage_slug": "test_second", "stage_name": "Test Second",
        "contact_type": contact_type, "changed_at": "2030-01-01T12:00:00",
    }
    assert rows[other]["lead_stage"]["stage_id"] == first_id
    # Same answer as the single-contact endpoint.
    assert client.get(f"/contacts/{moved}/stage", headers=headers).json() == rows[moved]["lead_stage"]


def test_list_row_without_a_stage_is_null(client, login, lead_source_id):
    headers, _ = login("admin")
    contact_id = _create(client, headers, lead_source_id("website"), "consumer", "9100000003")
    with SessionLocal() as db:
        db.query(LeadStage).filter(LeadStage.contact_id == contact_id).delete()
        db.commit()

    assert _rows(client, headers, "consumer")[contact_id]["lead_stage"] is None
