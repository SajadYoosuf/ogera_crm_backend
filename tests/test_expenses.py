"""Expense claims: the manager review rules, the date filter, and the rejection notification.

WHAT THESE TESTS PROTECT:
    - A rejection stores the note AND tells the staff member why (in-app notification).
    - Approval only updates the claim; it does not send a notification.
    - The list can be narrowed by date. "date_to" includes the whole last day.
    - Staff cannot review claims.
"""
import datetime


def _create_expense(client, headers, title="Fuel to Kottakkal site", amount="850.00"):
    response = client.post("/expenses", headers=headers, json={
        "title": title, "category": "petrol", "amount": amount,
    })
    assert response.status_code == 201, response.text
    return response.json()


def _notifications_for(client, headers):
    response = client.get("/notifications", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_reject_saves_the_note_and_notifies_the_submitter(client, login):
    staff_headers, _ = login("anu")
    manager_headers, _ = login("manager")
    expense = _create_expense(client, staff_headers)

    rejected = client.post(
        f"/expenses/{expense['id']}/reject",
        headers=manager_headers,
        json={"rejection_reason": "Bill photo is unreadable"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"

    items = _notifications_for(client, staff_headers)
    assert len(items) == 1
    note = items[0]
    assert note["type"] == "expense_rejected"
    assert note["entity_type"] == "expense"
    assert note["entity_id"] == expense["id"]
    assert "Bill photo is unreadable" in note["message"]
    assert "850.00" in note["message"]


def test_approve_updates_the_claim_without_notifying(client, login):
    staff_headers, _ = login("anu")
    manager_headers, _ = login("manager")
    expense = _create_expense(client, staff_headers)

    approved = client.post(f"/expenses/{expense['id']}/approve", headers=manager_headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    assert _notifications_for(client, staff_headers) == []


def test_staff_cannot_approve_or_reject(client, login):
    staff_headers, _ = login("anu")
    expense = _create_expense(client, staff_headers)

    approve = client.post(f"/expenses/{expense['id']}/approve", headers=staff_headers)
    reject = client.post(
        f"/expenses/{expense['id']}/reject", headers=staff_headers, json={"rejection_reason": "nope"},
    )
    assert approve.status_code == 403
    assert reject.status_code == 403


def test_reject_refuses_a_blank_note_and_leaves_the_claim_pending(client, login):
    staff_headers, _ = login("anu")
    manager_headers, _ = login("manager")
    expense = _create_expense(client, staff_headers)

    response = client.post(
        f"/expenses/{expense['id']}/reject", headers=manager_headers, json={"rejection_reason": "   "},
    )
    assert response.status_code == 422

    still = client.get(f"/expenses/{expense['id']}", headers=manager_headers).json()
    assert still["status"] == "pending"
    assert _notifications_for(client, staff_headers) == []


def test_date_filter_includes_the_whole_last_day(client, login):
    staff_headers, _ = login("anu")
    manager_headers, _ = login("manager")
    expense = _create_expense(client, staff_headers)

    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)
    yesterday = today - datetime.timedelta(days=1)

    def ids(**params):
        response = client.get("/expenses", headers=manager_headers, params=params)
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["items"]]

    assert expense["id"] in ids(date_from=today.isoformat(), date_to=today.isoformat())
    assert expense["id"] in ids(date_from=yesterday.isoformat(), date_to=today.isoformat())
    assert expense["id"] not in ids(date_from=tomorrow.isoformat())
    assert expense["id"] not in ids(date_to=yesterday.isoformat())


def test_date_filter_rejects_a_backwards_range(client, login):
    manager_headers, _ = login("manager")
    response = client.get("/expenses", headers=manager_headers, params={
        "date_from": "2026-10-10", "date_to": "2026-10-01",
    })
    assert response.status_code == 422
