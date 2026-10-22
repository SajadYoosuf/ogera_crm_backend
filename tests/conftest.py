"""Shared fixtures: the real FastAPI app on an in-memory SQLite database (no MySQL needed).

`DATABASE_URL` is read when `database` is first imported, so it is set here before anything else loads.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SQL_ECHO", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_as_integer(type_, compiler, **kw):
    # SQLite only auto-increments a column typed exactly INTEGER.
    return "INTEGER"


from database import Base, SessionLocal, engine  # noqa: E402
from models import LeadSource, PipelineStage  # noqa: E402
from seed_data import MOBILE_APP, PASSWORD, email_of, seed  # noqa: E402


@pytest.fixture()
def app():
    import main

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
    return main.app


@pytest.fixture()
def client(app):
    return TestClient(app)


@pytest.fixture()
def login(client):
    """login("anu") -> (Authorization headers, employee json)."""

    def _login(key: str):
        response = client.post("/auth/login", json={"email": email_of(key), "password": PASSWORD}, headers=MOBILE_APP)
        assert response.status_code == 200, response.text
        body = response.json()
        return {"Authorization": f"Bearer {body['access_token']}"}, body["employee"]

    return _login


@pytest.fixture()
def lead_source_id():
    def _lookup(slug: str) -> int:
        with SessionLocal() as db:
            return db.query(LeadSource).filter(LeadSource.slug == slug).one().id

    return _lookup


@pytest.fixture()
def stage_id_for():
    """stage_id_for("new_lead", "consumer") -> stage_types.id."""
    def _lookup(slug: str, contact_type: str = "consumer") -> int:
        with SessionLocal() as db:
            row = db.query(PipelineStage).filter(
                PipelineStage.slug == slug,
                PipelineStage.contact_type == contact_type,
                PipelineStage.is_active == 1,
            ).first()
            assert row is not None, f"Stage '{slug}' for type '{contact_type}' not found — pipeline not seeded?"
            return row.id

    return _lookup
