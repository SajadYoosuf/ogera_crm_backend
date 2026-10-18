"""
One-time migration: delete all 'new_lead' stage log entries for consumer and partner contacts.
After this runs, those contacts have current_stage = null.

Run with: python scripts/remove_new_lead_stage.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from database import SessionLocal


def run(yes: bool = False):
    db = SessionLocal()
    try:
        # Count before
        count_q = text("""
            SELECT COUNT(*)
            FROM lead_stages lsl
            JOIN contacts c ON c.id = lsl.contact_id
            WHERE lsl.stage = 'new_lead'
              AND c.contact_type IN ('consumer', 'partner')
        """)
        count = db.execute(count_q).scalar()
        print(f"Found {count} new_lead entries for consumer/partner contacts.")

        if count == 0:
            print("Nothing to delete.")
            return

        if not yes:
            confirm = input(f"Delete all {count} entries? [y/N] ").strip().lower()
            if confirm != "y":
                print("Aborted.")
                return

        delete_q = text("""
            DELETE lsl
            FROM lead_stages lsl
            JOIN contacts c ON c.id = lsl.contact_id
            WHERE lsl.stage = 'new_lead'
              AND c.contact_type IN ('consumer', 'partner')
        """)
        result = db.execute(delete_q)
        db.commit()
        print(f"Deleted {result.rowcount} rows. Consumer and partner contacts now have null current_stage.")
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()
    run(yes=args.yes)
