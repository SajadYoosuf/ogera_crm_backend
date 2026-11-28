"""Shared fixtures: the real FastAPI app on an in-memory SQLite database (no MySQL needed).

Settings and credentials are read when the app is first imported, so `isolate()` runs here before anything
else loads. It ignores `.env`, forces SQLite and refuses connections that leave this machine.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from isolation import blocked_hosts, isolate  # noqa: E402

isolate()

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_as_integer(type_, compiler, **kw):
    # SQLite only auto-increments a column typed exactly INTEGER.
    return "INTEGER"


from sqlalchemy.dialects.postgresql import ARRAY, JSONB  # noqa: E402


@compiles(ARRAY, "sqlite")
def _array_as_text(type_, compiler, **kw):
    # leads.tags is a PostgreSQL text[]; tests never write a value to it.
    return "TEXT"


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


from database import Base, SessionLocal, engine, DATABASE_URL  # noqa: E402
from models import LeadSource, PipelineStage  # noqa: E402
from seed_data import MOBILE_APP, PASSWORD, email_of, seed  # noqa: E402

# SQLite doesn't support PostgreSQL schemas. Map "public" → None so that
# public.tenants and public.employee_tenant_map are created as plain tables.
if DATABASE_URL.startswith("sqlite"):
    engine.update_execution_options(schema_translate_map={"public": None})


def pytest_terminal_summary(terminalreporter):
    if blocked_hosts:
        counts = ", ".join(f"{host} ({blocked_hosts.count(host)})" for host in sorted(set(blocked_hosts)))
        terminalreporter.write_sep("=", f"blocked outside connections: {counts}")


@pytest.fixture()
def app():
    import main

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
    return main.app


class _V1Client:
    """Wraps TestClient and prepends /api/v1 to every path so tests stay short."""

    _PREFIX = "/api/v1"

    def __init__(self, app):
        self._c = TestClient(app)

    def _url(self, path: str) -> str:
        return self._PREFIX + path

    def get(self, path, **kwargs):
        return self._c.get(self._url(path), **kwargs)

    def post(self, path, **kwargs):
        return self._c.post(self._url(path), **kwargs)

    def put(self, path, **kwargs):
        return self._c.put(self._url(path), **kwargs)

    def patch(self, path, **kwargs):
        return self._c.patch(self._url(path), **kwargs)

    def delete(self, path, **kwargs):
        return self._c.delete(self._url(path), **kwargs)


@pytest.fixture()
def client(app):
    return _V1Client(app)


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
