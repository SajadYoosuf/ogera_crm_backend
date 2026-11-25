"""A follow-up reassigned to a staff member makes that lead visible to them until the task is completed.

Staff = sales executives (anu, raj); heads = admin and manager. The lead stays assigned to its
original owner; only the open follow-up gives the new assignee access.
"""
import datetime

import pytest

from conftest import SessionLocal
from models import LeadStatus

FUTURE = (datetime.datetime.now() + datetime.timedelta(days=2)).replace(microsecond=0).isoformat()
_PHONES = iter(f"98{n:08d}" for n in range(30_000_000, 99_999_999))


@pytest.fixture(autouse=True)
def _statuses(app):
    with SessionLocal() as db:
        for i, (slug, name) in enumerate([("new", "New"), ("contacted", "Contacted")]):
            db.add(LeadStatus(slug=slug, name=name, sort_order=i, is_active=True))
        db.commit()


def _create_lead(client, headers, assigned_to):
    r = client.post("/leads?allow_duplicate=false", headers=headers,
                    json={"first_name": "Followup", "phone": next(_PHONES), "assigned_to": assigned_to})
    assert r.status_code == 201, r.text
    return r.json()


def _task(client, headers, lead_id, assigned_to):
    r = client.post(f"/leads/{lead_id}/interactions/task", headers=headers,
                    json={"activity_type": "phone_call", "due_at": FUTURE, "assigned_to": assigned_to})
    assert r.status_code == 201, r.text
    return r.json()


def _ids(client, headers):
    return {lead["id"] for lead in client.get("/leads?limit=100", headers=headers).json()["items"]}


def test_reassigned_followup_puts_lead_on_new_staff_leads_page_until_completed(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    raj_h, raj = login("raj")
    lead = _create_lead(client, admin_h, anu["id"])
    task = _task(client, anu_h, lead["id"], anu["id"])

    assert lead["id"] not in _ids(client, raj_h)

    reassigned = client.patch(f"/leads/{lead['id']}/interactions/{task['id']}/reassign",
                              headers=admin_h, json={"assigned_to": raj["id"]})
    assert reassigned.status_code == 200, reassigned.text

    # Raj gets the lead on his list and can open it, although the lead itself belongs to anu.
    assert lead["id"] in _ids(client, raj_h)
    assert client.get(f"/leads/{lead['id']}", headers=raj_h).status_code == 200

    # Until the follow-up is completed.
    done = client.patch(f"/interactions/{task['id']}", headers=raj_h,
                        json={"status": "completed", "outcome": "done"})
    assert done.status_code == 200, done.text
    assert lead["id"] not in _ids(client, raj_h)
    assert client.get(f"/leads/{lead['id']}", headers=raj_h).status_code == 403


def test_original_owner_keeps_lead_after_followup_moves_away(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    raj_h, raj = login("raj")
    lead = _create_lead(client, admin_h, anu["id"])
    task = _task(client, anu_h, lead["id"], anu["id"])

    client.patch(f"/leads/{lead['id']}/interactions/{task['id']}/reassign",
                 headers=admin_h, json={"assigned_to": raj["id"]})

    assert lead["id"] in _ids(client, anu_h)


def test_completed_followup_does_not_grant_access(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    raj_h, raj = login("raj")
    lead = _create_lead(client, admin_h, raj["id"])
    task = _task(client, admin_h, lead["id"], anu["id"])
    client.patch(f"/interactions/{task['id']}", headers=anu_h, json={"status": "completed", "outcome": "done"})

    assert lead["id"] not in _ids(client, anu_h)
    assert client.get(f"/leads/{lead['id']}", headers=anu_h).status_code == 403
    assert lead["id"] in _ids(client, raj_h)  # the owner keeps the lead


def test_open_followup_grants_staff_access_to_a_lead_they_do_not_own(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    raj_h, raj = login("raj")
    lead = _create_lead(client, admin_h, raj["id"])
    _task(client, admin_h, lead["id"], anu["id"])

    assert lead["id"] in _ids(client, anu_h)
    assert client.get(f"/leads/{lead['id']}", headers=anu_h).status_code == 200


def test_unrelated_staff_still_cannot_see_the_lead(client, login):
    admin_h, _ = login("admin")
    anu_h, anu = login("anu")
    _, raj = login("raj")
    lead = _create_lead(client, admin_h, raj["id"])
    _task(client, admin_h, lead["id"], anu["id"])

    sam_h, _ = login("sam")
    assert lead["id"] not in _ids(client, sam_h)
    assert client.get(f"/leads/{lead['id']}", headers=sam_h).status_code == 403
