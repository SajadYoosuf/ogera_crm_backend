"""The home dashboard summary: its numbers, the period windows, role scope and the edge cases behind them."""
import datetime

from business_time import IST_OFFSET, ist_day_start_utc, today_ist
from database import SessionLocal
from models import Contact, FollowupSchedule, LeadStage, StageType
from routes.dashboard import window_for


def _contact(client, headers, sources, assigned_to, name="Dash Lead", phone="9811111111"):
    body = {
        "contact_type": "consumer", "name": name, "phone": phone, "district": "Calicut",
        "address": "House 2, Beach Road", "lead_source_id": sources("meta"), "assigned_to": assigned_to,
        "consumer_profile": {"house_stage": "ready"},
    }
    response = client.post("/contacts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _summary(client, headers, period="month"):
    response = client.get("/dashboard/summary", params={"range": period}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _backdate_contact(contact_id, ist_naive):
    """Store a contact's created_at as the UTC value of an IST wall-clock time."""
    with SessionLocal() as db:
        db.query(Contact).filter(Contact.id == contact_id).update({"created_at": ist_naive - IST_OFFSET})
        db.commit()


def _stage_id(slug, contact_type="consumer"):
    with SessionLocal() as db:
        return db.query(StageType).filter(StageType.slug == slug, StageType.contact_type == contact_type).one().id


def _insert_followup(assigned_to, due_at, status="todo", contact_id=None, title="Call back"):
    with SessionLocal() as db:
        db.add(FollowupSchedule(contact_id=contact_id, assigned_to=assigned_to, due_at=due_at, status=status, title=title, priority="medium"))
        db.commit()


def _today_at(hour, days=0):
    day = today_ist() + datetime.timedelta(days=days)
    return datetime.datetime.combine(day, datetime.time(hour, 0))


# --- the period windows ------------------------------------------------------------

def test_month_window_compares_with_the_same_days_of_last_month():
    win = window_for("month", datetime.date(2026, 10, 17))
    assert (win.start, win.end) == (datetime.date(2026, 10, 1), datetime.date(2026, 10, 17))
    assert (win.previous_start, win.previous_end) == (datetime.date(2026, 9, 1), datetime.date(2026, 9, 17))


def test_month_window_clamps_to_a_shorter_previous_month():
    win = window_for("month", datetime.date(2026, 3, 31))
    assert win.previous_end == datetime.date(2026, 2, 28)


def test_first_day_of_month_compares_with_the_first_day_of_last_month():
    win = window_for("month", datetime.date(2026, 3, 1))
    assert (win.previous_start, win.previous_end) == (datetime.date(2026, 2, 1), datetime.date(2026, 2, 1))


def test_seven_day_window_has_seven_days_and_the_seven_before_it():
    win = window_for("7d", datetime.date(2026, 10, 17))
    assert (win.start, win.end) == (datetime.date(2026, 10, 11), datetime.date(2026, 10, 17))
    assert (win.previous_start, win.previous_end) == (datetime.date(2026, 10, 4), datetime.date(2026, 10, 10))


def test_all_time_has_no_window_and_no_comparison():
    win = window_for("all", datetime.date(2026, 10, 17))
    assert win == (None, None, None, None)


# --- access and input ----------------------------------------------------------------

def test_summary_needs_sign_in(client):
    assert client.get("/dashboard/summary").status_code in (401, 403)


def test_unknown_range_is_rejected(client, login):
    headers, _ = login("admin")
    assert client.get("/dashboard/summary", params={"range": "yearly"}, headers=headers).status_code == 422


def test_empty_database_gives_zeros_and_empty_lists(client, login):
    headers, _ = login("admin")
    body = _summary(client, headers, "all")
    assert body["kpis"]["total_contacts"] == 0
    assert body["kpis"]["new_leads"] == {"value": 0, "previous": None, "change": None}
    assert body["kpis"]["open_followups"] == {"value": 0, "overdue": 0}
    assert body["stages"] == [] and body["followups"] == [] and body["recent_leads"] == []
    assert body["range_label"] == "All time"


# --- new leads and the change from the previous period ----------------------------

def test_new_leads_and_change_against_the_previous_period(client, login, lead_source_id):
    headers, anu = login("anu")
    _contact(client, headers, lead_source_id, anu["id"], name="Today A", phone="9811111101")
    _contact(client, headers, lead_source_id, anu["id"], name="Today B", phone="9811111102")
    old = _contact(client, headers, lead_source_id, anu["id"], name="Last week", phone="9811111103")
    _backdate_contact(old["id"], _today_at(12, days=-10))  # 10 days ago: in the previous 7 days, not this week

    body = _summary(client, headers, "7d")
    assert body["kpis"]["new_leads"] == {"value": 2, "previous": 1, "change": 1}
    assert body["kpis"]["total_contacts"] == 3


def test_contact_created_just_after_midnight_ist_counts_for_today(client, login, lead_source_id):
    """IST midnight is 18:30 UTC the day before. A lead at 00:30 IST must count for that IST day, not the UTC one."""
    headers, admin = login("admin")
    contact = _contact(client, headers, lead_source_id, admin["id"], name="Early", phone="9811111111")
    _backdate_contact(contact["id"], _today_at(0, days=0) + datetime.timedelta(minutes=30))
    assert _summary(client, headers, "today")["kpis"]["new_leads"]["value"] == 1

    _backdate_contact(contact["id"], _today_at(0, days=0) - datetime.timedelta(minutes=30))
    assert _summary(client, headers, "today")["kpis"]["new_leads"]["value"] == 0


def test_all_time_has_no_change_figure(client, login, lead_source_id):
    headers, admin = login("admin")
    _contact(client, headers, lead_source_id, admin["id"])
    kpi = _summary(client, headers, "all")["kpis"]["new_leads"]
    assert kpi["value"] == 1 and kpi["previous"] is None and kpi["change"] is None


# --- role scope -----------------------------------------------------------------------

def test_staff_see_only_their_own_contacts_and_leads(client, login, lead_source_id):
    headers_anu, anu = login("anu")
    headers_admin, admin = login("admin")
    _, raj = login("raj")
    _contact(client, headers_anu, lead_source_id, anu["id"], name="Anu's", phone="9811111111")
    _contact(client, headers_admin, lead_source_id, raj["id"], name="Raj's", phone="9811111112")

    staff = _summary(client, headers_anu, "all")
    assert staff["kpis"]["total_contacts"] == 1
    assert [lead["name"] for lead in staff["recent_leads"]] == ["Anu's"]

    everyone = _summary(client, headers_admin, "all")
    assert everyone["kpis"]["total_contacts"] == 2
    assert len(everyone["recent_leads"]) == 2


def test_staff_see_only_follow_ups_assigned_to_them(client, login):
    headers_anu, anu = login("anu")
    headers_admin, _ = login("admin")
    _, raj = login("raj")
    _insert_followup(anu["id"], _today_at(9, days=-1), title="Anu overdue")
    _insert_followup(raj["id"], _today_at(9, days=-1), title="Raj overdue")

    staff = _summary(client, headers_anu)
    assert staff["followups_total"] == 1
    assert staff["followups"][0]["title"] == "Anu overdue"

    assert _summary(client, headers_admin)["followups_total"] == 2


# --- won -------------------------------------------------------------------------------

def test_a_contact_that_reaches_won_twice_counts_once(client, login, lead_source_id):
    headers, admin = login("admin")
    contact = _contact(client, headers, lead_source_id, admin["id"])
    for slug in ("won", "negotiation", "won"):
        response = client.post(f"/contacts/{contact['id']}/stages",
                               json={"stage_id": _stage_id(slug), "changed_by": admin["id"]}, headers=headers)
        assert response.status_code == 201, response.text

    assert _summary(client, headers, "today")["kpis"]["won"]["value"] == 1


def test_won_counts_the_stage_move_not_the_lead_creation_date(client, login, lead_source_id):
    headers, admin = login("admin")
    contact = _contact(client, headers, lead_source_id, admin["id"])
    client.post(f"/contacts/{contact['id']}/stages", json={"stage_id": _stage_id("won"), "changed_by": admin["id"]}, headers=headers)
    with SessionLocal() as db:  # the move happened 10 days ago, the lead was created today
        db.query(LeadStage).filter(LeadStage.contact_id == contact["id"], LeadStage.stage_id == _stage_id("won")).update(
            {"changed_at": ist_day_start_utc(today_ist()) - datetime.timedelta(days=10)})
        db.commit()

    body = _summary(client, headers, "today")
    assert body["kpis"]["won"]["value"] == 0
    assert _summary(client, headers, "30d")["kpis"]["won"]["value"] == 1


def test_a_deleted_contact_is_not_counted_as_won(client, login, lead_source_id):
    headers, admin = login("admin")
    contact = _contact(client, headers, lead_source_id, admin["id"])
    client.post(f"/contacts/{contact['id']}/stages", json={"stage_id": _stage_id("won"), "changed_by": admin["id"]}, headers=headers)
    assert client.delete(f"/contacts/{contact['id']}", headers=headers).status_code == 204
    assert _summary(client, headers, "today")["kpis"]["won"]["value"] == 0


# --- stage breakdown ---------------------------------------------------------------------

def test_stage_breakdown_counts_current_stage_and_sorts_by_count(client, login, lead_source_id):
    headers, admin = login("admin")
    a = _contact(client, headers, lead_source_id, admin["id"], name="A", phone="9811111101")
    b = _contact(client, headers, lead_source_id, admin["id"], name="B", phone="9811111102")
    _contact(client, headers, lead_source_id, admin["id"], name="C", phone="9811111103")
    # a moves to First Call and b to Interested; c stays at New Lead. Each lead is counted at its current stage only.
    client.post(f"/contacts/{b['id']}/stages", json={"stage_id": _stage_id("interested"), "changed_by": admin["id"]}, headers=headers)
    client.post(f"/contacts/{a['id']}/stages", json={"stage_id": _stage_id("first_call"), "changed_by": admin["id"]}, headers=headers)

    stages = _summary(client, headers, "month")["stages"]
    # equal counts sort by name
    assert stages == [
        {"slug": "first_call", "name": "First Call", "count": 1},
        {"slug": "interested", "name": "Interested", "count": 1},
        {"slug": "new_lead", "name": "New Lead", "count": 1},
    ]


def test_same_stage_name_in_two_pipelines_is_one_bar(client, login, lead_source_id):
    headers, admin = login("admin")
    _contact(client, headers, lead_source_id, admin["id"], name="Consumer", phone="9811111101")
    partner_body = {
        "contact_type": "partner", "name": "Partner", "phone": "9811111102", "district": "Calicut",
        "address": "Road 1", "lead_source_id": lead_source_id("meta"), "assigned_to": admin["id"],
        "partner_profile": {"profession_type": "plumber"},
    }
    response = client.post("/contacts", json=partner_body, headers=headers)
    assert response.status_code == 201, response.text

    new_lead = [s for s in _summary(client, headers, "month")["stages"] if s["slug"] == "new_lead"]
    assert new_lead == [{"slug": "new_lead", "name": "New Lead", "count": 2}]


def test_contact_without_a_stage_is_counted_as_no_stage(client, login, lead_source_id):
    headers, admin = login("admin")
    _contact(client, headers, lead_source_id, admin["id"])
    with SessionLocal() as db:  # a row that was imported without any stage
        db.add(Contact(contact_type="consumer", name="Bare", phone="9822222222", phone_key="9822222222", assigned_to=admin["id"]))
        db.commit()

    stages = {s["slug"]: s["count"] for s in _summary(client, headers, "month")["stages"]}
    assert stages == {"new_lead": 1, "no_stage": 1}


# --- follow-ups -------------------------------------------------------------------------

def test_follow_ups_show_today_and_overdue_but_not_future_or_done(client, login, lead_source_id):
    headers, anu = login("anu")
    contact = _contact(client, headers, lead_source_id, anu["id"])
    _insert_followup(anu["id"], _today_at(9, days=-3), contact_id=contact["id"], title="Old")
    _insert_followup(anu["id"], _today_at(18), contact_id=contact["id"], title="Later today")
    _insert_followup(anu["id"], _today_at(10, days=2), contact_id=contact["id"], title="Next week")
    _insert_followup(anu["id"], _today_at(9, days=-1), status="completed", contact_id=contact["id"], title="Done")

    body = _summary(client, headers)
    assert body["followups_total"] == 2
    assert body["kpis"]["open_followups"] == {"value": 2, "overdue": 1}
    assert [f["title"] for f in body["followups"]] == ["Old", "Later today"]  # most overdue first
    assert body["followups"][0]["overdue_days"] == 3
    assert body["followups"][1]["overdue_days"] == 0


def test_follow_ups_of_a_deleted_contact_are_hidden(client, login, lead_source_id):
    headers, admin = login("admin")
    contact = _contact(client, headers, lead_source_id, admin["id"])
    _insert_followup(admin["id"], _today_at(9, days=-1), contact_id=contact["id"])
    assert _summary(client, headers)["followups_total"] == 1
    client.delete(f"/contacts/{contact['id']}", headers=headers)
    assert _summary(client, headers)["followups_total"] == 0


def test_standalone_task_without_a_contact_is_listed(client, login):
    headers, admin = login("admin")
    _insert_followup(admin["id"], _today_at(9, days=-1), contact_id=None, title="Order stock")
    item = _summary(client, headers)["followups"][0]
    assert item["title"] == "Order stock" and item["contact_id"] is None and item["contact_name"] is None


def test_only_the_six_most_overdue_follow_ups_are_listed_but_the_total_is_full(client, login):
    headers, admin = login("admin")
    for day in range(1, 9):
        _insert_followup(admin["id"], _today_at(9, days=-day), title=f"Task {day}")
    body = _summary(client, headers)
    assert body["followups_total"] == 8
    assert len(body["followups"]) == 6
    assert body["followups"][0]["title"] == "Task 8"


# --- recent leads -----------------------------------------------------------------------

def test_recent_leads_are_newest_first_and_capped_at_six(client, login, lead_source_id):
    headers, admin = login("admin")
    for i in range(7):
        _contact(client, headers, lead_source_id, admin["id"], name=f"Lead {i}", phone=f"98100000{i:02d}")
    recent = _summary(client, headers, "all")["recent_leads"]
    assert len(recent) == 6
    assert recent[0]["name"] == "Lead 6"
    assert recent[0]["stage_slug"] == "new_lead" and recent[0]["stage_name"] == "New Lead"
    assert recent[0]["owner_name"] == "Ada Admin"


def test_recent_leads_ignore_the_period(client, login, lead_source_id):
    headers, admin = login("admin")
    old = _contact(client, headers, lead_source_id, admin["id"], name="Old lead", phone="9811111111")
    _backdate_contact(old["id"], _today_at(12, days=-400))
    names = [lead["name"] for lead in _summary(client, headers, "today")["recent_leads"]]
    assert names == ["Old lead"]
