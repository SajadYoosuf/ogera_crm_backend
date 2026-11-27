"""Routes must read tenant data through the tenant session.

get_db opens a session without `SET search_path`, so on PostgreSQL it looks in the public schema and fails with
`relation "roles" does not exist`. The SQLite test database has no schemas, so requests cannot show that; this
checks the route sources instead.
"""
import re
from pathlib import Path

ROUTES = Path(__file__).resolve().parent.parent / "routes"


def test_no_route_uses_the_session_without_a_tenant_schema():
    offenders = [
        path.name
        for path in sorted(ROUTES.glob("*.py"))
        if re.search(r"Depends\(\s*get_db\s*\)", path.read_text(encoding="utf-8-sig"))
    ]
    assert offenders == [], f"use get_tenant_db instead of get_db in: {', '.join(offenders)}"
