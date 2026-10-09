"""Product API tests on an in-memory SQLite database (no MySQL needed)."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auth import get_current_user  # noqa: E402
from database import Base, get_db  # noqa: E402
import models  # noqa: E402,F401
from routes.products import router  # noqa: E402

PRODUCT = {
    "name": "200 LTR Vertical Stainless Steel Water Tank",
    "slug": "200-ltr-vertical-stainless-steel-water-tank",
    "capacity_litres": 200,
    "orientation": "vertical",
    "mrp": "6000",
    "sale_price": "5400",
    "main_image_url": "https://example.com/tank.jpg",
    "features": ["SS304 food grade"],
}


@pytest.fixture()
def client():
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

    user = SimpleNamespace(is_active=True, role="admin")
    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_current_user] = lambda: user
    test_client = TestClient(app)
    test_client.user = user
    return test_client


def test_create_computes_discount(client):
    response = client.post("/products", json=PRODUCT)
    assert response.status_code == 201
    body = response.json()
    assert body["discount_percent"] == "10.00" or float(body["discount_percent"]) == 10.0
    assert float(body["you_save"]) == 600.0


def test_duplicate_slug_and_sku_conflict(client):
    assert client.post("/products", json={**PRODUCT, "sku": "A1"}).status_code == 201
    assert client.post("/products", json={**PRODUCT, "sku": "B2"}).status_code == 409
    assert client.post("/products", json={**PRODUCT, "slug": "other", "sku": "A1"}).status_code == 409


def test_sale_price_above_mrp_rejected(client):
    assert client.post("/products", json={**PRODUCT, "sale_price": "7000"}).status_code == 422
    product_id = client.post("/products", json=PRODUCT).json()["id"]
    assert client.patch(f"/products/{product_id}", json={"sale_price": "9000"}).status_code == 422


def test_options_and_patch(client):
    brand = client.post("/products/options", json={"kind": "brand", "name": "Ventino"}).json()
    again = client.post("/products/options", json={"kind": "brand", "name": "Ventino"}).json()
    assert brand["id"] == again["id"]
    product_id = client.post("/products", json={**PRODUCT, "brand_id": brand["id"]}).json()["id"]
    patched = client.patch(f"/products/{product_id}", json={"tagline": "Hi"}).json()
    assert patched["brand"]["name"] == "Ventino" and patched["tagline"] == "Hi"
    # a brand id can't be used as a category
    assert client.patch(f"/products/{product_id}", json={"category_id": brand["id"]}).status_code == 422


def test_soft_delete_hides_product(client):
    product_id = client.post("/products", json=PRODUCT).json()["id"]
    assert client.delete(f"/products/{product_id}").status_code == 204
    assert client.get(f"/products/{product_id}").status_code == 404
    assert client.get("/products").json()["total"] == 0


def test_non_editor_can_read_but_not_write(client):
    client.post("/products", json=PRODUCT)
    client.user.role = "sales_executive"
    assert client.get("/products").status_code == 200
    assert client.post("/products", json={**PRODUCT, "slug": "x"}).status_code == 403
