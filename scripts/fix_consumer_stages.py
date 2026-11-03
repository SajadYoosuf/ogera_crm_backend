"""
Migration: fix consumer pipeline stage order and remove stale stages.

WHAT THIS DOES
--------------
The consumer pipeline_stages table had two problems from overlapping seed scripts:

1. Duplicate / conflicting rows:
   - 'quoted'   (id=5)  — old slug from scripts/seed.py; superseded by 'quotation' (id=21)
   - 'referral' (id=8)  — old slug; superseded by 'after_sales_referring' (id=22)
   Both are deactivated (is_active=0). They are NOT deleted because lead_stage_log
   rows may reference them for historical records.

2. Wrong order numbers — 'connected' and 'aware' both had order=3, and several
   other stages had colliding order values. This script sets the correct order:

   1  new_lead
   2  first_call
   3  connected
   4  aware
   5  interested
   6  quotation
   7  negotiating
   8  won
   9  lost
   10 after_sales_referring

USAGE
-----
Run from the project root:
    python scripts/fix_consumer_stages.py

Add --dry-run to preview changes without writing them:
    python scripts/fix_consumer_stages.py --dry-run
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from database import SessionLocal

# (id, desired_order, action)
# action: "order" = just fix order, "deactivate" = turn off
CHANGES = [
    (1,  1,  "order"),       # new_lead
    (2,  2,  "order"),       # first_call
    (9,  3,  "order"),       # connected  ← was sharing order=3 with aware
    (3,  4,  "order"),       # aware
    (4,  5,  "order"),       # interested
    (21, 6,  "order"),       # quotation  ← keep
    (10, 7,  "order"),       # negotiating (id 10)
    (6,  8,  "order"),       # won
    (7,  9,  "order"),       # lost
    (22, 10, "order"),       # after_sales_referring
    (5,  0,  "deactivate"),  # quoted — old slug, replaced by quotation
    (8,  0,  "deactivate"),  # referral — old slug, replaced by after_sales_referring
]


def fix_consumer_stages(dry_run: bool = False) -> None:
    db = SessionLocal()
    try:
        # Fetch current state for the report
        rows = {
            r.id: r
            for r in db.execute(text(
                "SELECT id, slug, name, `order`, is_active "
                "FROM pipeline_stages WHERE contact_type = 'consumer'"
            )).fetchall()
        }

        print("Changes to apply:")
        for stage_id, new_order, action in CHANGES:
            row = rows.get(stage_id)
            if row is None:
                print(f"  WARNING: id={stage_id} not found — skipping")
                continue
            if action == "deactivate":
                status = "already inactive" if not row.is_active else "will deactivate"
                print(f"  DEACTIVATE  id={stage_id}  slug={row.slug!r}  [{status}]")
            else:
                changed = "unchanged" if row.order == new_order else f"{row.order} → {new_order}"
                print(f"  order={new_order}  id={stage_id}  slug={row.slug!r}  [order {changed}]")

        if dry_run:
            print("\nDry-run mode — no changes made.")
            return

        for stage_id, new_order, action in CHANGES:
            if rows.get(stage_id) is None:
                continue
            if action == "deactivate":
                db.execute(
                    text("UPDATE pipeline_stages SET is_active = 0 WHERE id = :id"),
                    {"id": stage_id},
                )
            else:
                db.execute(
                    text("UPDATE pipeline_stages SET `order` = :order WHERE id = :id"),
                    {"order": new_order, "id": stage_id},
                )

        db.commit()
        print("\nDone. Consumer pipeline is now:")
        updated = db.execute(text(
            "SELECT id, slug, name, `order`, is_active "
            "FROM pipeline_stages WHERE contact_type = 'consumer' "
            "ORDER BY `order`, id"
        )).fetchall()
        for r in updated:
            status = "active" if r.is_active else "inactive"
            print(f"  order={r.order}  id={r.id}  slug={r.slug!r}  name={r.name!r}  [{status}]")

    except Exception as exc:
        db.rollback()
        print(f"\nERROR — rolled back: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    fix_consumer_stages(dry_run=dry_run)
