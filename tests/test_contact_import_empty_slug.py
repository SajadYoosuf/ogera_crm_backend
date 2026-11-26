"""Contact import when lead sources have no slug, as in a database whose slug column holds NULL."""
import pytest
from sqlalchemy import event
from sqlalchemy.orm.attributes import set_committed_value

from database import SessionLocal
from models import Contact, LeadSource


@pytest.fixture()
def empty_slugs():
    """Every lead source loads with slug None. The test table is NOT NULL, so the value is blanked on load."""
    def _blank(source, _context):
        set_committed_value(source, "slug", None)

    event.listen(LeadSource, "load", _blank)
    yield
    event.remove(LeadSource, "load", _blank)


def _source_name(source_id: int) -> str:
    with SessionLocal() as db:
        return db.get(LeadSource, source_id).name


def _post(client, headers, path, content, **form):
    return client.post(
        f"/contacts/import/{path}",
        headers=headers,
        files={"file": ("contacts.csv", content.encode(), "text/csv")},
        data=form,
    )


def test_analyze_and_commit_match_sources_by_name(client, login, lead_source_id, request):
    headers, _ = login("admin")
    source_id = lead_source_id("meta")
    name = _source_name(source_id)
    request.getfixturevalue("empty_slugs")
    # "-" normalises to an empty key: it must stay unknown, not match a source that has no slug.
    content = f"Name,Phone,Lead source\nNamed,9100004001,{name}\nDash,9100004002,-\n"

    report = _post(client, headers, "analyze", content)

    assert report.status_code == 200, report.text
    assert report.json()["summary"]["error"] == 0
    issues = {row["name"]: row["issues"] for row in report.json()["rows"]}
    assert not any("lead source" in issue for issue in issues.get("Named", []))
    assert "Unknown lead source '-'" in issues["Dash"]

    result = _post(client, headers, "commit", content, mapping='{"0":"name","1":"phone","2":"lead_source"}')

    assert result.status_code == 200, result.text
    assert result.json()["created"] == 2
    with SessionLocal() as db:
        saved = dict(db.query(Contact.name, Contact.lead_source_id).filter(Contact.name.in_(["Named", "Dash"])))
    assert saved == {"Named": source_id, "Dash": None}
