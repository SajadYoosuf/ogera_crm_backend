"""A newly created employee can log in, which needs a row in public.employee_tenant_map."""
from sqlmodel import select

from database import SessionLocal
from models import EmployeeLookup
from seed_data import MOBILE_APP

NEW = {"name": "Nila New", "email": "Nila.New@Example.com", "password": "Passw0rd!", "role": "sales_executive"}


def test_created_employee_can_log_in(client, login):
    admin, _ = login("admin")

    created = client.post("/employees", json=NEW, headers=admin)
    assert created.status_code == 201, created.text

    signed_in = client.post("/auth/login", json={"email": NEW["email"], "password": NEW["password"]}, headers=MOBILE_APP)
    assert signed_in.status_code == 200, signed_in.text


def test_address_used_by_another_company_is_refused(client, login):
    admin, _ = login("admin")
    with SessionLocal() as db:
        db.add(EmployeeLookup(email=NEW["email"].lower(), tenant_id=99))
        db.commit()

    refused = client.post("/employees", json=NEW, headers=admin)

    assert refused.status_code == 409, refused.text
    with SessionLocal() as db:
        row = db.scalar(select(EmployeeLookup).where(EmployeeLookup.email == NEW["email"].lower()))
        assert row.tenant_id == 99
    listed = client.get("/employees", params={"search": "Nila"}, headers=admin).json()
    assert listed["total"] == 0
