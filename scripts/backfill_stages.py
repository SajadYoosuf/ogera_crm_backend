"""
Backfill an initial lead_stages row for contacts that have none.
every contact starts as 'new_lead'. Stage time = contact.created_at.
    python scripts/backfill_stages.py           # dry run
    python scripts/backfill_stages.py --commit
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import SessionLocal, engine
import models  # noqa: F401
from models import Contact, ConsumerProfile, Employee, LeadStage

OWNER_EMAIL = "admin@ogera.com"


def main():
    engine.echo = False
    db = SessionLocal()
    try:
        owner = db.query(Employee).filter_by(email=OWNER_EMAIL).one()
        rows = (
            db.query(Contact, ConsumerProfile.awareness_mode)
            .outerjoin(ConsumerProfile, ConsumerProfile.contact_id == Contact.id)
            .outerjoin(LeadStage, LeadStage.contact_id == Contact.id)
            .filter(LeadStage.id.is_(None), Contact.deleted_at.is_(None))
            .all()
        )
        counts = {"aware": 0, "new_lead": 0}
        for contact, mode in rows:
            stage = "new_lead"
            counts[stage] += 1
            db.add(LeadStage(
                contact_id=contact.id, stage=stage,
                changed_by=owner.id, changed_at=contact.created_at,
            ))
        print(counts)
        if "--commit" in sys.argv:
            db.commit()
            print("Committed.")
        else:
            db.rollback()
            print("Dry run - pass --commit to write.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
