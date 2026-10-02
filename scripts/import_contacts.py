"""
Import legacy contacts export into contacts + consumer_profiles.
Run from project root:
    python scripts/import_contacts.py contacts_2026-09-30.json           # dry run
    python scripts/import_contacts.py contacts_2026-09-30.json --commit  # write
"""

import datetime
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

from database import SessionLocal, engine
import models  # noqa: F401
from models import Contact, ConsumerProfile, Employee, LeadSource

DEFAULT_OWNER_EMAIL = "admin@ogera.com"
MISSING_DISTRICT = "Unknown"
EXTRA_LEAD_SOURCES = [
    {"name": "Offline", "slug": "offline"},
    {"name": "Justdial", "slug": "justdial"},
]


def clean(v):
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def normalize_phone(phone: str, country_code: str | None) -> str:
    p = re.sub(r"[\s\-()]", "", phone)
    if p.startswith("+"):
        return p
    cc = clean(country_code) or "+91"
    if cc == "+91":
        return "+91" + p if len(p) == 10 else "+" + p
    # Gulf rows often already carry their own country code (971/966/968...)
    if p.startswith(("971", "966", "968", "974", "965", "973")):
        return "+" + p
    return cc + p


def parse_tank(v):
    """Return (type, capacity) from the legacy current_tank field."""
    v = clean(v)
    if not v:
        return None, None
    m = re.fullmatch(r"(\d+)\s*[lL]?", v)
    if m:
        return None, int(m.group(1))
    return v, None


def parse_dt(v):
    return datetime.datetime.strptime(v, "%Y-%m-%d %H:%M:%S") if v else None


def ensure_lead_sources(db) -> dict[str, int]:
    for s in EXTRA_LEAD_SOURCES:
        if not db.query(LeadSource).filter_by(slug=s["slug"]).first():
            db.add(LeadSource(**s, is_active=True))
    db.flush()
    return {ls.name.lower(): ls.id for ls in db.query(LeadSource).all()}


def main():
    path = next(a for a in sys.argv[1:] if not a.startswith("--"))
    commit = "--commit" in sys.argv
    engine.echo = False
    rows = json.load(open(path, encoding="utf-8"))

    db = SessionLocal()
    try:
        owner = db.query(Employee).filter_by(email=DEFAULT_OWNER_EMAIL).one()
        sources = ensure_lead_sources(db)
        stats = Counter()
        seen_phones = Counter()

        for r in rows:
            phone = normalize_phone(r["phone"], r.get("country_code"))
            seen_phones[phone] += 1
            source = clean(r.get("source"))
            district = clean(r.get("district"))
            stats["no_district"] += district is None
            stats["no_name"] += clean(r.get("name")) is None

            contact = Contact(
                contact_type="consumer",
                name=(clean(r.get("name")) or "Unknown")[:150],
                phone=phone,
                district=(district or MISSING_DISTRICT)[:80],
                address=clean(r.get("address")),
                assigned_to=owner.id,
                lead_source_id=sources[source.lower()] if source else None,
                notes=clean(r.get("remarks")),
                created_at=parse_dt(r.get("created_at")),
                updated_at=parse_dt(r.get("updated_at")),
            )
            tank_type, tank_cap = parse_tank(r.get("current_tank"))
            contact.consumer_profile = ConsumerProfile(
                # lead -> sales-ready (0); awareness -> needs education (1)
                awareness_mode=1 if r["contact_type"] == "awareness" else 0,
                house_stage=clean(r.get("house_stage")),
                current_tank_type=tank_type,
                current_tank_capacity=tank_cap,
            )
            db.add(contact)
            stats["contacts"] += 1

        dupes = {p: n for p, n in seen_phones.items() if n > 1}
        print(dict(stats), "duplicate phones:", dupes)
        if commit:
            db.commit()
            print("Committed.")
        else:
            db.rollback()
            print("Dry run - nothing written. Pass --commit to write.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
