"""
Migration: remove dealer pipeline stages from the database.

WHY THIS EXISTS
---------------
The `dealer` contact type was removed from the codebase (see commit 7516458).
Code-side the `ContactType` enum now only has `consumer` and `partner`, and
`DEFAULT_STAGES` in settings_catalog.py never seeds dealer rows. But the
`pipeline_stages` table may still hold rows with `contact_type = 'dealer'`
from the original seed. This script deletes them safely.

WHAT IT DOES
------------
1. Reports how many dealer stage rows exist (dry-run preview).
2. Deletes any `lead_stage_log` rows that reference those stages (foreign key
   would block a direct delete of the stage rows).
3. Deletes the dealer `pipeline_stages` rows.

USAGE
-----
Run from the project root:
    python scripts/remove_dealer_stages.py

Add --dry-run to see what would be deleted without changing anything:
    python scripts/remove_dealer_stages.py --dry-run
"""

import sys
from pathlib import Path

# Allow imports from the project root when running directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from database import SessionLocal


def remove_dealer_stages(dry_run: bool = False) -> None:
    db = SessionLocal()
    try:
        # 1. Find the dealer stage IDs
        dealer_stage_rows = db.execute(
            text("SELECT id, slug, name FROM pipeline_stages WHERE contact_type = 'dealer'")
        ).fetchall()

        if not dealer_stage_rows:
            print("No dealer pipeline stages found in the database. Nothing to do.")
            return

        print(f"Found {len(dealer_stage_rows)} dealer pipeline stage(s):")
        for row in dealer_stage_rows:
            print(f"  id={row.id}  slug={row.slug!r}  name={row.name!r}")

        dealer_stage_ids = [row.id for row in dealer_stage_rows]
        id_list = ", ".join(str(i) for i in dealer_stage_ids)

        # 2. Count lead_stage_log rows that reference these stages
        log_count = db.execute(
            text(f"SELECT COUNT(*) FROM lead_stage_log WHERE stage_id IN ({id_list})")
        ).scalar()

        print(f"\n{log_count} lead_stage_log row(s) reference dealer stages.")

        if dry_run:
            print("\nDry-run mode — no changes made.")
            print(f"Would delete {log_count} lead_stage_log row(s).")
            print(f"Would delete {len(dealer_stage_rows)} pipeline_stages row(s).")
            return

        # 3. Delete the referencing log rows first (foreign key constraint)
        if log_count:
            db.execute(text(f"DELETE FROM lead_stage_log WHERE stage_id IN ({id_list})"))
            print(f"Deleted {log_count} lead_stage_log row(s).")

        # 4. Delete the dealer pipeline stages
        db.execute(text("DELETE FROM pipeline_stages WHERE contact_type = 'dealer'"))
        print(f"Deleted {len(dealer_stage_rows)} pipeline_stages row(s) with contact_type='dealer'.")

        db.commit()
        print("\nDone.")

    except Exception as exc:
        db.rollback()
        print(f"\nERROR — rolled back: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    remove_dealer_stages(dry_run=dry_run)
