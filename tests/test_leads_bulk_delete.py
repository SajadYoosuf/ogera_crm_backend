"""POST /leads/bulk-delete: soft-deletes the leads matching the list filters (admin/manager only)."""
import datetime

import pytest

from conftest import SessionLocal
from models import LeadStatus

_PHONES = iter(f"97{n:08d}" for n in range(10_000_000, 99_999_999))


@pytest.fixture(autouse=True)
def _statuses(app):
    with SessionLocal() as db:
        for i, (slug, name) in enumerate([("new", "New"), ("contacted", "Contacted"), ("lost", "Lost")]):
            db.add(LeadStatus(slug=slug, name=name, sort_order=i, is_active=True))
        db.commit()


def _create_lead(client, headers, assigned_to=None, **extra):
    body = {"first_name": "Bulk", "phone": next(_PHONES), **extra}
    if assigned_to is not None:
        body["assigned_to"] = assigned_to
    r = client.post("/leads?allow_duplicate=false", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _live_ids(client, headers):
    return {l["id"] for l in client.get("/leads", headers=headers, params={"limit": 100}).json()["items"]}


def test_staff_cannot_bulk_delete(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    lead = _create_lead(client, admin_h, assigned_to=anu["id"])
    r = client.post("/leads/bulk-delete", json={"assigned_to": anu["id"]}, headers=anu_h)
    assert r.status_code == 403, r.text
    assert lead["id"] in _live_ids(client, admin_h)


def test_empty_filter_is_rejected_and_deletes_nothing(client, login):
    admin_h, _ = login("admin")
    lead = _create_lead(client, admin_h)
    r = client.post("/leads/bulk-delete", json={}, headers=admin_h)
    assert r.status_code == 400, r.text
    assert lead["id"] in _live_ids(client, admin_h)


def test_deletes_only_leads_matching_the_filter(client, login):
    admin_h, _ = login("admin")
    _, anu = login("anu")
    _, raj = login("raj")
    anu_lead = _create_lead(client, admin_h, assigned_to=anu["id"])
    raj_lead = _create_lead(client, admin_h, assigned_to=raj["id"])

    r = client.post("/leads/bulk-delete", json={"assigned_to": anu["id"]}, headers=admin_h)
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": 1}

    live = _live_ids(client, admin_h)
    assert anu_lead["id"] not in live
    assert raj_lead["id"] in live


def test_search_filter_and_count_match_the_list(client, login):
    admin_h, _ = login("admin")
    needle = "Zqxbulk"
    _create_lead(client, admin_h, first_name=needle)
    _create_lead(client, admin_h, first_name=needle)
    _create_lead(client, admin_h, first_name="Unrelated")

    listed = client.get("/leads", headers=admin_h, params={"search": needle}).json()["total"]
    r = client.post("/leads/bulk-delete", json={"search": needle}, headers=admin_h)
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == listed == 2
    assert client.get("/leads", headers=admin_h, params={"search": needle}).json()["total"] == 0


def test_date_range_filter(client, login):
    admin_h, _ = login("admin")
    lead = _create_lead(client, admin_h, first_name="Dated")
    created = datetime.date.fromisoformat(lead["created_at"][:10])
    far_past = created - datetime.timedelta(days=400)

    r = client.post("/leads/bulk-delete", json={"date_to": far_past.isoformat()}, headers=admin_h)
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 0
    assert lead["id"] in _live_ids(client, admin_h)
