"""Performance dashboard endpoints, exercised through the real app."""
import datetime

from database import SessionLocal
from models import StageType
from models.followup_schedule import FollowupSchedule

TODAY = datetime.date.today()
YESTERDAY = TODAY - datetime.timedelta(days=1)


def _contact(client, headers, sources, source="meta", **overrides):
    body = {
        "contact_type": "consumer",
        "name": "Arun Kumar",
        "phone": "9876543210",
        "district": "Calicut",
        "address": "House 4, MG Road",
        "lead_source_id": sources(source),
        "consumer_profile": {"house_stage": "ready"},
    }
    body.update(overrides)
    response = client.post("/contacts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _stage_id(slug: str, contact_type: str = "consumer") -> int:
    with SessionLocal() as db:
        row = db.query(StageType).filter(StageType.slug == slug, StageType.contact_type == contact_type).first()
        assert row is not None, f"Stage '{slug}' for type '{contact_type}' not seeded"
        return row.id


def _stage(client, headers, user, contact_id, stage, contact_type="consumer", **extra):
    body = {"stage_id": _stage_id(stage, contact_type), "changed_by": user["id"], **extra}
    response = client.post(f"/contacts/{contact_id}/stages", json=body, headers=headers)
    assert response.status_code == 201, response.text


def _task(client, headers, user, contact_id, when):
    """A pending follow-up. The API refuses past dates, so an overdue one is backdated in the database."""
    due_dt = datetime.datetime.combine(max(when, TODAY), datetime.time(9, 0))
    if due_dt < datetime.datetime.now():
        due_dt = datetime.datetime.now() + datetime.timedelta(hours=1)
    body = {"channel": "call", "due_at": due_dt.isoformat(), "assigned_to": user["id"]}
    response = client.post(f"/contacts/{contact_id}/followups", json=body, headers=headers)
    assert response.status_code == 201, response.text
    if when < TODAY:
        with SessionLocal() as db:
            past_dt = datetime.datetime.combine(when, datetime.time(9, 0))
            db.query(FollowupSchedule).filter(FollowupSchedule.id == response.json()["id"]).update({"due_at": past_dt})
            db.commit()


def _log(client, headers, contact_id):
    response = client.post(f"/contacts/{contact_id}/activities", json={"activity_type": "phone_call", "notes": "spoke"}, headers=headers)
    assert response.status_code == 201, response.text


def test_endpoints_need_a_token(client):
    for path in ("summary", "team", "pipeline", "lead-sources"):
        assert client.get(f"/performance/{path}").status_code in (401, 403)


def test_team_is_manager_only(client, login):
    anu, _ = login("anu")
    assert client.get("/performance/team", headers=anu).status_code == 403
    manager, _ = login("manager")
    assert client.get("/performance/team", headers=manager).status_code == 200


def test_bad_range_is_rejected(client, login):
    admin, _ = login("admin")
    response = client.get("/performance/summary", params={"start_date": "2026-10-10", "end_date": "2026-10-01"}, headers=admin)
    assert response.status_code == 400


def test_empty_period_returns_zeros_and_all_stages(client, login):
    admin, _ = login("admin")
    summary = client.get("/performance/summary", headers=admin).json()
    assert summary["total_new_contacts"] == 0 and summary["overdue_followups"] == 0
    stages = client.get("/performance/pipeline", headers=admin).json()
    assert len(stages) == 10 and all(row["count"] == 0 for row in stages)
    assert client.get("/performance/lead-sources", headers=admin).json() == []


def test_summary_scopes_to_the_salesperson(client, login, lead_source_id):
    anu, anu_user = login("anu")
    raj, raj_user = login("raj")
    admin, _ = login("admin")
    mine = _contact(client, anu, lead_source_id, assigned_to=anu_user["id"])
    _contact(client, raj, lead_source_id, phone="9876543211", assigned_to=raj_user["id"])
    _log(client, anu, mine["id"])
    _stage(client, anu, anu_user, mine["id"], "won")
    _task(client, anu, anu_user, mine["id"], YESTERDAY)

    own = client.get("/performance/summary", headers=anu).json()
    assert own["scope"] == "self"
    assert (own["total_new_contacts"], own["total_activities"], own["deals_won"], own["overdue_followups"]) == (1, 1, 1, 1)

    company = client.get("/performance/summary", headers=admin).json()
    assert company["scope"] == "team" and company["total_new_contacts"] == 2


def test_date_filter_excludes_other_periods(client, login, lead_source_id):
    admin, _ = login("admin")
    _contact(client, admin, lead_source_id)
    old = {"start_date": "2020-01-01", "end_date": "2020-01-31"}
    assert client.get("/performance/summary", params=old, headers=admin).json()["total_new_contacts"] == 0
    assert client.get("/performance/lead-sources", params=old, headers=admin).json() == []
    today = {"start_date": TODAY.isoformat(), "end_date": TODAY.isoformat()}
    assert client.get("/performance/summary", params=today, headers=admin).json()["total_new_contacts"] == 1


def test_team_scorecard_sorts_by_overdue(client, login, lead_source_id):
    admin, _ = login("admin")
    anu, anu_user = login("anu")
    raj, raj_user = login("raj")
    anu_contact = _contact(client, anu, lead_source_id, assigned_to=anu_user["id"])
    raj_contact = _contact(client, raj, lead_source_id, phone="9876543211", assigned_to=raj_user["id"])
    _task(client, raj, raj_user, raj_contact["id"], YESTERDAY)
    _task(client, raj, raj_user, raj_contact["id"], YESTERDAY)
    _task(client, anu, anu_user, anu_contact["id"], TODAY + datetime.timedelta(days=2))
    _stage(client, anu, anu_user, anu_contact["id"], "won")

    rows = client.get("/performance/team", headers=admin).json()
    assert [row["name"] for row in rows] == ["Raj Field", "Anu Sales"]  # admin and manager are not scored
    raj_row, anu_row = rows
    assert (raj_row["followups_overdue"], raj_row["followups_pending"]) == (2, 2)
    assert (anu_row["followups_overdue"], anu_row["followups_pending"], anu_row["deals_won_period"]) == (0, 1, 1)
    assert anu_row["contacts_assigned"] == 1 and anu_row["contacts_added_period"] == 1


def test_pipeline_uses_the_current_stage_only(client, login, lead_source_id):
    admin, admin_user = login("admin")
    contact = _contact(client, admin, lead_source_id)
    _stage(client, admin, admin_user, contact["id"], "first_call")
    _stage(client, admin, admin_user, contact["id"], "connected")
    counts = {row["stage"]: row["count"] for row in client.get("/performance/pipeline", headers=admin).json()}
    assert counts["connected"] == 1 and counts["new_lead"] == 0 and counts["first_call"] == 0


def test_lead_sources_count_converted_and_unknown(client, login, lead_source_id):
    admin, admin_user = login("admin")
    won = _contact(client, admin, lead_source_id, source="meta")
    _stage(client, admin, admin_user, won["id"], "won")
    _contact(client, admin, lead_source_id, source="meta", phone="9876543211")
    rows = client.get("/performance/lead-sources", headers=admin).json()
    assert rows[0] == {"source": "Meta", "count": 2, "converted": 1}
