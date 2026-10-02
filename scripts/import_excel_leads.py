"""
Import the JSON produced by scripts/prepare_excel_leads.py into contacts,
consumer_profiles and lead_stages. Contacts already in the DB (same last 10
phone digits) are never duplicated: they are skipped, except that a buyer
register hit adds a 'won' stage when the contact has none.
Run from project root:
    python scripts/import_excel_leads.py leads_prepared.json           # dry run
    python scripts/import_excel_leads.py leads_prepared.json --commit  # write
"""

import datetime
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import SessionLocal, engine
import models  # noqa: F401
from models import Contact, ConsumerProfile, Employee, LeadSource, LeadStage

DEFAULT_OWNER_EMAIL = "admin@ogera.com"
MISSING_DISTRICT = "Unknown"


def parse_dt(v):
    return datetime.datetime.fromisoformat(v) if v else None


def main():
    path = next(a for a in sys.argv[1:] if not a.startswith("--"))
    commit = "--commit" in sys.argv
    engine.echo = False
    rows = json.load(open(path, encoding="utf-8"))

    db = SessionLocal()
    try:
        owner = db.query(Employee).filter_by(email=DEFAULT_OWNER_EMAIL).one()
        sources = {s.slug: s.id for s in db.query(LeadSource).all()}
        existing = {}
        for c in db.query(Contact).filter(Contact.deleted_at.is_(None)).all():
            existing.setdefault(c.phone[-10:], c)
        won_ids = {s.contact_id for s in db.query(LeadStage).filter_by(stage="won")}

        stats = Counter()
        for r in rows:
            k = r["phone"][-10:]
            won_at = parse_dt(r["won_date"] + " 00:00:00") if r.get("won_date") else None
            hit = existing.get(k)

            if hit:
                if won_at and hit.id not in won_ids:
                    db.add(LeadStage(contact_id=hit.id, stage="won", changed_by=owner.id, changed_at=won_at))
                    won_ids.add(hit.id)
                    stats["existing_marked_won"] += 1
                else:
                    stats["skipped_already_in_db"] += 1
                continue

            created_at = parse_dt(r.get("created_at")) or won_at
            contact = Contact(
                contact_type="consumer",
                name=r["name"],
                phone=r["phone"],
                phone_alt=r.get("phone_alt"),
                district=(r.get("district") or MISSING_DISTRICT)[:80],
                address=(r.get("address") or None) and r["address"][:255],
                assigned_to=owner.id,
                created_by=owner.id,
                lead_source_id=sources.get(r["source"]) if r.get("source") else None,
                notes=r.get("notes"),
            )
            if created_at:
                contact.created_at = created_at
                contact.updated_at = created_at
            contact.consumer_profile = ConsumerProfile(
                awareness_mode=0,  # sales-ready, like the earlier legacy import
                house_stage=r.get("house_stage"),
                current_tank_type=r.get("current_tank_type"),
            )
            if won_at is None or r["origin"] == "meta":
                contact.lead_stages.append(
                    LeadStage(stage="new_lead", changed_by=owner.id, changed_at=created_at or datetime.datetime.now())
                )
            if won_at:
                contact.lead_stages.append(LeadStage(stage="won", changed_by=owner.id, changed_at=won_at))
            db.add(contact)
            existing[k] = contact
            stats[f"new_{r['origin']}"] += 1
            stats["new_with_unknown_district"] += contact.district == MISSING_DISTRICT

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
