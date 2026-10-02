"""
Import legacy professionals export into contacts (partner) + partner_profiles.
Run from project root:
    python scripts/import_professionals.py professionals_2026-09-30.json           # dry run
    python scripts/import_professionals.py professionals_2026-09-30.json --commit  # write
"""

import datetime
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import SessionLocal, engine
import models  # noqa: F401
from models import Contact, PartnerProfile, Employee, LeadSource

# Owner must not be admin / manager / support
EXCLUDED_ROLES = ("admin", "manager", "support")
OWNER_EMAIL = None  # None -> the single active employee outside EXCLUDED_ROLES
MISSING_DISTRICT = "Unknown"  # contacts.district is NOT NULL
LEAD_SOURCE_SLUG = "offline"
PROFESSION_TYPES = {"contractor": "Contractor", "engineer": "Engineer", "plumber": "Plumber", "other": "Other"}


def clean(v):
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def normalize_phone(phone: str) -> str:
    p = re.sub(r"[\s\-()+]", "", phone)
    if len(p) == 11:  # legacy typos: one stray trailing digit
        p = p[:10]
    return "+91" + p if len(p) == 10 else "+" + p


def parse_dt(v):
    return datetime.datetime.strptime(v, "%Y-%m-%d %H:%M:%S") if v else None


def main():
    path = next(a for a in sys.argv[1:] if not a.startswith("--"))
    commit = "--commit" in sys.argv
    engine.echo = False
    rows = json.load(open(path, encoding="utf-8"))

    db = SessionLocal()
    try:
        q = db.query(Employee).filter(
            Employee.is_active == 1, Employee.deleted_at.is_(None), Employee.role.notin_(EXCLUDED_ROLES)
        )
        if OWNER_EMAIL:
            q = q.filter_by(email=OWNER_EMAIL)
        owners = q.all()
        if len(owners) != 1:
            raise SystemExit(f"Expected exactly one eligible owner, found {[(o.id, o.name, o.role) for o in owners]}")
        owner = owners[0]
        source = db.query(LeadSource).filter_by(slug=LEAD_SOURCE_SLUG).one()
        print(f"Owner: {owner.id} {owner.name} ({owner.role}); lead source: {source.name}")

        existing = {c.phone for c in db.query(Contact.phone).all()}
        stats = Counter()
        for r in rows:
            phone = normalize_phone(r["phone"])
            if phone in existing:
                stats["skipped_existing_phone"] += 1
                print("skip existing phone:", phone, r["name"])
                continue
            existing.add(phone)
            stats["trimmed_phone"] += len(re.sub(r"\D", "", r["phone"])) == 11

            ptype = clean(r.get("type"))
            profession = PROFESSION_TYPES.get((ptype or "").lower(), ptype) or "Other"
            place = clean(r.get("place"))
            # `place` repeats the trade for most rows; only keep it when it is a real work area
            work_area = place if place and place.lower() not in ("contactor", "contractor", "engineer", "plumber") else None
            stats["work_area"] += work_area is not None

            contact = Contact(
                contact_type="partner",
                name=clean(r["name"])[:150],
                phone=phone,
                district=MISSING_DISTRICT,
                assigned_to=owner.id,
                lead_source_id=source.id,
                latitude=None,
                longitude=None,
                created_at=parse_dt(r.get("created_at")),
                updated_at=parse_dt(r.get("updated_at")),
            )
            contact.partner_profile = PartnerProfile(profession_type=profession, work_area=work_area)
            db.add(contact)
            stats["contacts"] += 1
            stats[f"type:{profession}"] += 1

        print(dict(stats))
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
