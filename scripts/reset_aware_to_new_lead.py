"""
Move contacts whose current stage is 'aware' back to 'new_lead'.
Stage rows are append-only, so this adds a new_lead row; the old aware row stays in the history.
    python scripts/reset_aware_to_new_lead.py           # dry run
    python scripts/reset_aware_to_new_lead.py --commit
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import func
from database import SessionLocal, engine
import models  # noqa: F401
from models import Contact, Employee, LeadStage

CHANGED_BY_EMAIL = "admin@ogera.com"


def main():
    engine.echo = False
    db = SessionLocal()
    try:
        changed_by = db.query(Employee).filter_by(email=CHANGED_BY_EMAIL).one()
        latest = db.query(func.max(LeadStage.id)).group_by(LeadStage.contact_id).subquery()
        rows = (
            db.query(LeadStage)
            .join(Contact, Contact.id == LeadStage.contact_id)
            .filter(LeadStage.id.in_(db.query(latest)), LeadStage.stage == "aware", Contact.deleted_at.is_(None))
            .all()
        )
        for row in rows:
            db.add(LeadStage(contact_id=row.contact_id, stage="new_lead", changed_by=changed_by.id))
        print(f"{len(rows)} contact(s) currently in 'aware' -> 'new_lead'.")
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
