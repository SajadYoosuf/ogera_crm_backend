"""Double-submit protection (qa_checklist.yaml, cross_cutting section).

FINDING, not a passing confirmation: posting the identical follow-up payload twice, back to
back, creates TWO rows — confirmed 2026-10-18 via a direct probe (two 201s, ids 1 and 2, same
contact/channel/due_at/assigned_to/notes). There is no idempotency key, no short dedup window,
nothing. This is a real backend gap, not just a "the UI should disable the button" issue — a
flaky network retry or a slow double-tap on mobile hits this exactly the same way.

Left as xfail per backend-test-audit's rule (don't silently weaken a test to force green;
report the bug and leave it visible) rather than fixed here — the right fix (idempotency key
header? a few-second dedup window on identical payloads? disable-on-submit is a UI-only
band-aid that doesn't cover retries) is a product decision, not a harness one.
"""
import datetime

import pytest


def _contact(client, headers, lead_source_id, phone):
    return client.post("/contacts", headers=headers, json={
        "contact_type": "consumer", "name": "DupTest", "phone": phone, "district": "Calicut",
        "address": "H1", "lead_source_id": lead_source_id("meta"), "consumer_profile": {"house_stage": "ready"},
    }).json()


@pytest.mark.xfail(reason="known bug, filed 2026-10-18: no dedup protection on /contacts/{id}/followups — "
                           "two identical rapid POSTs create two rows", strict=True)
def test_posting_the_same_followup_twice_creates_only_one_record(client, login, lead_source_id):
    headers, me = login("anu")
    contact = _contact(client, headers, lead_source_id, "9199999991")
    due = (datetime.datetime.now() + datetime.timedelta(hours=2)).isoformat()
    payload = {"channel": "call", "due_at": due, "assigned_to": me["id"], "notes": "dup-check"}

    first = client.post(f"/contacts/{contact['id']}/followups", headers=headers, json=payload)
    second = client.post(f"/contacts/{contact['id']}/followups", headers=headers, json=payload)
    assert first.status_code == 201

    # Desired behaviour: the identical immediate resubmission is a no-op (or 409), not a new row.
    assert second.json()["id"] == first.json()["id"], \
        "a second identical submission created a new row instead of being treated as a duplicate"


@pytest.mark.xfail(reason="same root cause as above: standalone tasks (not tied to a contact) have "
                           "no dedup protection either", strict=True)
def test_posting_the_same_standalone_task_twice_creates_only_one_record(client, login):
    headers, me = login("anu")
    due = (datetime.datetime.now() + datetime.timedelta(hours=2)).isoformat()
    payload = {"channel": "call", "due_at": due, "assigned_to": me["id"], "notes": "dup-check-task"}

    first = client.post("/tasks", headers=headers, json=payload)
    second = client.post("/tasks", headers=headers, json=payload)
    assert first.status_code == 201
    assert second.json()["id"] == first.json()["id"]
