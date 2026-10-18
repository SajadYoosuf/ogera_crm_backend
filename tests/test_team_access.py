"""Team and support access: team leads see their team, team leads assign only to their team, support sees customers."""
import datetime

from auth import get_password_hash
from database import SessionLocal
from models import Employee, StageType
from seed_data import PASSWORD, email_of


def _add_staff(key, role, team_lead_id=None):
    with SessionLocal() as db:
        employee = Employee(
            name=key.title(), email=email_of(key), password_hash=get_password_hash(PASSWORD),
            role=role, team_lead_id=team_lead_id,
        )
        db.add(employee)
        db.commit()
        return employee.id


def _stage_id(slug, contact_type="consumer"):
    with SessionLocal() as db:
        return db.query(StageType).filter(StageType.slug == slug, StageType.contact_type == contact_type).one().id


def _contact(client, headers, sources, phone, assigned_to):
    # assigned_to is set explicitly: auto-assignment could pick a team lead and blur the scope under test.
    response = client.post("/contacts", headers=headers, json={
        "contact_type": "consumer", "name": f"Lead {phone}", "phone": phone, "district": "Calicut",
        "address": "House 4, MG Road", "lead_source_id": sources("meta"),
        "consumer_profile": {"house_stage": "ready"}, "assigned_to": assigned_to,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _followup(client, headers, contact_id, assignee_id, notes):
    return client.post(f"/contacts/{contact_id}/followups", headers=headers, json={
        "channel": "call", "due_at": (datetime.datetime.now() + datetime.timedelta(days=1)).isoformat(),
        "assigned_to": assignee_id, "notes": notes,
    })


def _team(client, login, admin, sources):
    """A team lead with one member, and a second employee outside the team. Each creates one contact."""
    lead_id = _add_staff("tl_north", "team_lead")
    member_id = _add_staff("mem_north", "sales_executive", team_lead_id=lead_id)
    outsider_id = _add_staff("mem_south", "sales_executive")
    lead_h, _ = login("tl_north")
    member_h, _ = login("mem_north")
    outsider_h, _ = login("mem_south")
    mine = _contact(client, member_h, sources, "9100000001", assigned_to=member_id)
    theirs = _contact(client, outsider_h, sources, "9100000002", assigned_to=outsider_id)
    return {
        "lead_id": lead_id, "member_id": member_id, "outsider_id": outsider_id,
        "lead_h": lead_h, "member_h": member_h, "outsider_h": outsider_h,
        "mine": mine, "theirs": theirs, "admin_h": admin[0], "admin_id": admin[1]["id"],
    }


def _admin(login):
    headers, employee = login("admin")
    return headers, employee


def test_team_lead_sees_own_team_contacts_only(client, login, lead_source_id):
    admin = _admin(login)
    team = _team(client, login, (admin[0], admin[1]), lead_source_id)

    assert client.get(f"/contacts/{team['mine']}", headers=team["lead_h"]).status_code == 200
    assert client.get(f"/contacts/{team['theirs']}", headers=team["lead_h"]).status_code == 404


def test_removing_the_team_link_removes_access(client, login, lead_source_id):
    admin = _admin(login)
    team = _team(client, login, admin, lead_source_id)

    response = client.put(f"/employees/{team['member_id']}", headers=admin[0], json={"team_lead_id": None})
    assert response.status_code == 200, response.text
    assert client.get(f"/contacts/{team['mine']}", headers=team["lead_h"]).status_code == 404


def test_team_link_must_point_to_an_active_team_lead(client, login, lead_source_id):
    admin = _admin(login)
    team = _team(client, login, admin, lead_source_id)

    not_a_lead = client.put(f"/employees/{team['member_id']}", headers=admin[0],
                            json={"team_lead_id": team["outsider_id"]})
    assert not_a_lead.status_code == 422, not_a_lead.text

    own = client.put(f"/employees/{team['member_id']}", headers=admin[0],
                     json={"team_lead_id": team["member_id"]})
    assert own.status_code == 400, own.text


def test_demoting_a_team_lead_unlinks_the_members(client, login, lead_source_id):
    admin = _admin(login)
    team = _team(client, login, admin, lead_source_id)

    demoted = client.put(f"/employees/{team['lead_id']}", headers=admin[0], json={"role": "sales_executive"})
    assert demoted.status_code == 200, demoted.text
    member = client.get(f"/employees/{team['member_id']}", headers=admin[0]).json()
    assert member["team_lead_id"] is None


def test_support_sees_customers_and_its_own_contacts_only(client, login, lead_source_id):
    admin = _admin(login)
    team = _team(client, login, admin, lead_source_id)
    _add_staff("sup_one", "support")
    support_h, _ = login("sup_one")

    customer = team["mine"]
    client.post(f"/contacts/{customer}/stages", headers=admin[0],
                json={"stage_id": _stage_id("won"), "changed_by": admin[1]["id"]})

    assert client.get(f"/contacts/{customer}", headers=support_h).status_code == 200
    assert client.get(f"/contacts/{team['theirs']}", headers=support_h).status_code == 404


def test_team_lead_assigns_only_within_the_team(client, login, lead_source_id):
    admin = _admin(login)
    team = _team(client, login, admin, lead_source_id)

    inside = _followup(client, team["lead_h"], team["mine"], team["member_id"], "to member")
    assert inside.status_code == 201, inside.text

    outside = _followup(client, team["lead_h"], team["mine"], team["outsider_id"], "to outsider")
    assert outside.status_code == 403, outside.text


def test_team_lead_task_list_shows_team_tasks_only(client, login, lead_source_id):
    admin = _admin(login)
    team = _team(client, login, admin, lead_source_id)
    assert _followup(client, admin[0], team["mine"], team["member_id"], "team-task").status_code == 201
    assert _followup(client, admin[0], team["theirs"], team["outsider_id"], "other-task").status_code == 201

    notes = {item["notes"] for item in client.get("/tasks", headers=team["lead_h"], params={"status": "open"}).json()["items"]}
    assert "team-task" in notes
    assert "other-task" not in notes


def _partner_body(phone):
    return {"contact_type": "partner", "name": f"Plumber {phone}", "phone": phone, "district": "Calicut",
            "address": "Shop 1", "partner_profile": {"profession_type": "plumber", "work_area": "Calicut"}}


def test_tele_caller_creates_consumers_but_not_partners(client, login, lead_source_id):
    _add_staff("tele_one", "tele_caller")
    headers, _ = login("tele_one")
    consumer = _contact(client, headers, lead_source_id, "9100000010", assigned_to=None)
    assert consumer
    partner = client.post("/contacts", headers=headers, json={
        **_partner_body("9100000011"), "lead_source_id": lead_source_id("meta")})
    assert partner.status_code == 403, partner.text


def test_support_cannot_create_contacts_or_move_stages(client, login, lead_source_id):
    admin = _admin(login)
    sup_id = _add_staff("sup_two", "support")
    support_h, _ = login("sup_two")
    created = client.post("/contacts", headers=support_h, json={
        "contact_type": "consumer", "name": "X", "phone": "9100000012", "district": "Calicut",
        "address": "House", "lead_source_id": lead_source_id("meta")})
    assert created.status_code == 403, created.text

    # Assigned to support so the contact is visible; the stage guard is what must refuse the move.
    contact = _contact(client, admin[0], lead_source_id, "9100000013", assigned_to=sup_id)
    moved = client.post(f"/contacts/{contact}/stages", headers=support_h,
                        json={"stage_id": _stage_id("aware"), "changed_by": admin[1]["id"]})
    assert moved.status_code == 403, moved.text


def test_field_executive_moves_only_early_stages(client, login, lead_source_id):
    admin = _admin(login)
    raj_id = client.get("/employees", headers=admin[0], params={"search": "raj@"}).json()["items"][0]["id"]
    raj_h, raj = login("raj")
    contact = _contact(client, admin[0], lead_source_id, "9100000014", assigned_to=raj_id)

    early = client.post(f"/contacts/{contact}/stages", headers=raj_h,
                        json={"stage_id": _stage_id("aware"), "changed_by": raj["id"]})
    assert early.status_code == 201, early.text
    late = client.post(f"/contacts/{contact}/stages", headers=raj_h,
                       json={"stage_id": _stage_id("negotiation"), "changed_by": raj["id"]})
    assert late.status_code == 403, late.text


def test_support_schedules_follow_ups_for_itself_only(client, login, lead_source_id):
    admin = _admin(login)
    sup_id = _add_staff("sup_three", "support")
    sup_h, _ = login("sup_three")
    team = _team(client, login, admin, lead_source_id)
    contact = _contact(client, admin[0], lead_source_id, "9100000015", assigned_to=sup_id)
    own = _followup(client, sup_h, contact, sup_id, "support-own")
    assert own.status_code == 201, own.text
    other = _followup(client, sup_h, contact, team["member_id"], "support-other")
    assert other.status_code == 403, other.text


def test_export_is_limited_to_admin_manager_and_team_lead(client, login, lead_source_id):
    admin = _admin(login)
    team = _team(client, login, admin, lead_source_id)
    assert client.get("/contacts/export", headers=admin[0], params={"format": "csv"}).status_code == 200
    assert client.get("/contacts/export", headers=team["lead_h"], params={"format": "csv"}).status_code == 200
    assert client.get("/contacts/export", headers=team["member_h"], params={"format": "csv"}).status_code == 403


def test_permission_matrix_reports_team_and_support_levels(client, login):
    admin = _admin(login)
    matrix = client.get("/settings/roles", headers=admin[0]).json()
    features = {f["key"]: f["levels"] for f in matrix["features"]}
    assert features["contacts"]["team_lead"]["level"] == "team"
    assert features["contacts"]["support"]["level"] == "customers"
    assert features["export"]["team_lead"]["level"] == "team"
    assert features["export"]["sales_executive"]["level"] == "none"
