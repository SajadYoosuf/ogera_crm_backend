"""Sales register: list, search, add, edit, delete and role limits, on in-memory SQLite (no MySQL needed)."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auth import get_current_user  # noqa: E402
from database import Base, get_db  # noqa: E402
from routes.sales import router  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_as_integer(type_, compiler, **kw):
    # SQLite only auto-increments a column typed exactly INTEGER.
    return "INTEGER"


def _make_client(role: str) -> TestClient:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    app = FastAPI()
    app.include_router(router)

    def db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = db
    user = SimpleNamespace(is_active=True, role=role)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture()
def admin():
    return _make_client("admin")


def test_create_then_list_newest_purchase_first(admin):
    admin.post("/sales", json={"name": "Old", "purchase_date": "2026-01-02"})
    admin.post("/sales", json={"name": "New", "purchase_date": "2026-10-01", "purchased_product": "SS Tank 500L"})
    body = admin.get("/sales").json()
    assert body["total"] == 2
    assert [item["name"] for item in body["items"]] == ["New", "Old"]
    assert body["items"][0]["purchased_product"] == "SS Tank 500L"


def test_phone_is_normalised_and_bad_phone_is_refused(admin):
    created = admin.post("/sales", json={"name": "Anil", "phone": "+91 99472 70717"}).json()
    assert created["phone"] == "+919947270717"  # same normalised form as contacts
    assert admin.post("/sales", json={"name": "Anil", "phone": "12"}).status_code == 422


def test_search_matches_name_phone_and_product(admin):
    admin.post("/sales", json={"name": "Anil Kumar", "phone": "9947270717", "purchased_product": "Plastic 1000L"})
    admin.post("/sales", json={"name": "Meera", "phone": "9000000001", "address": "Kozhikode"})
    assert admin.get("/sales", params={"search": "anil"}).json()["total"] == 1
    assert admin.get("/sales", params={"search": "9000000001"}).json()["total"] == 1
    assert admin.get("/sales", params={"search": "plastic"}).json()["total"] == 1
    assert admin.get("/sales", params={"search": "kozhikode"}).json()["total"] == 1
    assert admin.get("/sales", params={"search": "nobody"}).json()["total"] == 0


def test_paging_reports_the_full_total(admin):
    for i in range(3):
        admin.post("/sales", json={"name": f"Buyer {i}"})
    page = admin.get("/sales", params={"skip": 2, "limit": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 1


def test_edit_and_delete(admin):
    sale = admin.post("/sales", json={"name": "Anil"}).json()
    updated = admin.put(f"/sales/{sale['id']}", json={"address": "Calicut"}).json()
    assert updated["address"] == "Calicut" and updated["name"] == "Anil"
    assert admin.put(f"/sales/{sale['id']}", json={"name": ""}).status_code == 422
    assert admin.delete(f"/sales/{sale['id']}").status_code == 204
    assert admin.get(f"/sales/{sale['id']}").status_code == 404


def test_product_names_are_distinct(admin):
    admin.post("/sales", json={"name": "A", "purchased_product": "Tank X"})
    admin.post("/sales", json={"name": "B", "purchased_product": "Tank X"})
    admin.post("/sales", json={"name": "C"})
    assert admin.get("/sales/products").json() == ["Tank X"]
