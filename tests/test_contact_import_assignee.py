"""POST /contacts/import/commit: who the imported contacts are assigned to."""
import pytest

from database import SessionLocal
from models import Contact, Employee, Role

PHONES = [f"91000030{n:02d}" for n in range(12)]


def _commit(client, headers, lead_source_id, **form):
    content = "Name,Phone\n" + "".join(f"Import {phone},{phone}\n" for phone in PHONES)
    return client.post(
        "/contacts/import/commit",
        headers=headers,
        files={"file": ("contacts.csv", content.encode(), "text/csv")},
        data={"mapping": '{"0":"name","1":"phone"}', "default_source_id": str(lead_source_id("meta")), **form},
    )


def _assignees() -> set[int]:
    with SessionLocal() as db:
        # By name, because the import stores phone numbers in its own normalised form.
        return {row[0] for row in db.query(Contact.assigned_to).filter(Contact.name.like("Import %"))}


def _sales_ids() -> list[int]:
    with SessionLocal() as db:
        rows = db.query(Employee.id).join(Role, Role.id == Employee.role_id).filter(Role.slug == "sales_executive")
        return sorted(row[0] for row in rows)


def test_automatic_assignment_uses_only_active_sales_staff(client, login, lead_source_id):
    headers, _ = login("admin")
    active, inactive, deleted = _sales_ids()
    with SessionLocal() as db:
        db.get(Employee, inactive).is_active = 0
        db.get(Employee, deleted).deleted_at = db.get(Employee, deleted).created_at
        db.commit()

    response = _commit(client, headers, lead_source_id)

    assert response.status_code == 200, response.text
    assert response.json()["created"] == len(PHONES)
    assert _assignees() == {active}
    # Imported contacts start in the New Lead column of the pipeline board.
    rows = client.get("/contacts?limit=100", headers=headers).json()["items"]
    assert {row["lead_stage"]["stage_slug"] for row in rows} == {"new_lead"}


@pytest.mark.parametrize("as_array", [False, True])
def test_chosen_employee_is_accepted_as_id_or_array(client, login, lead_source_id, as_array):
    headers, _ = login("admin")
    chosen = _sales_ids()[1]

    response = _commit(client, headers, lead_source_id, assigned_to=f"[{chosen}]" if as_array else str(chosen))

    assert response.status_code == 200, response.text
    assert _assignees() == {chosen}


@pytest.mark.parametrize("value", ["abc", "[]", '["5"]', "true", "1.5"])
def test_bad_assignee_is_rejected(client, login, lead_source_id, value):
    headers, _ = login("admin")

    response = _commit(client, headers, lead_source_id, assigned_to=value)

    assert response.status_code == 422, response.text
    assert _assignees() == set()


def test_no_active_sales_staff_gives_a_clear_message(client, login, lead_source_id):
    headers, _ = login("admin")
    ids = _sales_ids()
    with SessionLocal() as db:
        db.query(Employee).filter(Employee.id.in_(ids)).update({Employee.is_active: 0})
        db.commit()

    response = _commit(client, headers, lead_source_id)

    assert response.status_code == 422
    assert "No active sales staff" in response.json()["detail"]
