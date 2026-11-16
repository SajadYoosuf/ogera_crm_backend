"""One-time migration: rename enum category 'professional_type' → 'partner_type'.

WHAT THIS DOES
  Updates every row in enum_values where category = 'professional_type'
  to category = 'partner_type'.

  The rows themselves (plumber, engineer, contractor, etc.) are unchanged —
  only the category key that groups them is renamed.

WHY
  The old name "professional_type" came from an earlier data model where partners
  were called "professionals". The team now uses "partner" consistently throughout,
  so the settings category key should match.

WHEN TO RUN
  Run this ONCE after deploying the code change that renames
  PROFESSIONAL_TYPE → PARTNER_TYPE in settings_catalog.py.

  Running it twice is safe — the UPDATE touches 0 rows the second time.

HOW TO RUN
  From the ogera_crm_backend directory:
      uv run python scripts/migrate_partner_type_rename.py

WHAT NOT TO DO
  Do not add this to seed_defaults() or any startup hook — it must run once,
  not on every server restart.
"""

import sys
from pathlib import Path

# Make sure the backend package is on the path when run as a script.
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import SessionLocal
from models import EnumValue

OLD_CATEGORY = "professional_type"
NEW_CATEGORY = "partner_type"


def run() -> None:
    db = SessionLocal()
    try:
        rows = db.query(EnumValue).filter(EnumValue.category == OLD_CATEGORY).all()
        if not rows:
            print(f"No rows with category='{OLD_CATEGORY}' — nothing to migrate (already done or never seeded).")
            return

        for row in rows:
            row.category = NEW_CATEGORY

        db.commit()
        print(f"Renamed {len(rows)} row(s): category '{OLD_CATEGORY}' → '{NEW_CATEGORY}'")
        for r in rows:
            print(f"  id={r.id}  value={r.value!r}  label={r.label!r}")
    except Exception as exc:
        db.rollback()
        print(f"Migration failed — rolled back. Error: {exc}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    run()
