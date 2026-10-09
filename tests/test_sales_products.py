"""Sales linked to the products catalogue, on in-memory SQLite (no MySQL needed)."""
import datetime
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
from models import Product  # noqa: E402
from routes.sales import router  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_as_integer(type_, compiler, **kw):
    # SQLite only auto-increments a column typed exactly INTEGER.
    return "INTEGER"


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(Product(id=1, name="200 LTR Vertical Tank", slug="200-ltr"))
        db.add(Product(id=2, name="Gone Tank", slug="gone", deleted_at=datetime.datetime.now()))
        db.commit()

    app = FastAPI()
    app.include_router(router)

    def db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(is_active=True, role="admin")
    return TestClient(app)


def test_picking_a_product_links_it_and_copies_the_name(client):
    response = client.post("/sales", json={"name": "Anil", "product_id": 1, "purchased_product": "typo tank"})
    assert response.status_code == 201
    body = response.json()
    assert body["product_id"] == 1 and body["purchased_product"] == "200 LTR Vertical Tank"
    assert client.get("/sales", params={"product_id": 1}).json()["total"] == 1
    assert client.get("/sales", params={"product_id": 99}).json()["total"] == 0


def test_unknown_or_deleted_product_is_refused(client):
    assert client.post("/sales", json={"name": "A", "product_id": 99}).status_code == 400
    assert client.post("/sales", json={"name": "A", "product_id": 2}).status_code == 400


def test_free_text_still_works_without_a_link(client):
    body = client.post("/sales", json={"name": "Old buyer", "purchased_product": "SS Tank 500L"}).json()
    assert body["product_id"] is None and body["purchased_product"] == "SS Tank 500L"


def test_editing_the_text_unlinks_and_editing_other_fields_keeps_the_link(client):
    sale = client.post("/sales", json={"name": "Anil", "product_id": 1}).json()
    assert client.put(f"/sales/{sale['id']}", json={"address": "Calicut"}).json()["product_id"] == 1
    unlinked = client.put(f"/sales/{sale['id']}", json={"purchased_product": "Something else"}).json()
    assert unlinked["product_id"] is None and unlinked["purchased_product"] == "Something else"
    relinked = client.put(f"/sales/{sale['id']}", json={"product_id": 1}).json()
    assert relinked["product_id"] == 1 and relinked["purchased_product"] == "200 LTR Vertical Tank"
