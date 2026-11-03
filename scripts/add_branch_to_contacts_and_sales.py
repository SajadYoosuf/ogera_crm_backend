"""
Migration: add `branch` column to contacts and sales tables.

What this does:
  Adds a nullable ENUM column called `branch` to both tables.
  Allowed values: 'malappuram', 'kottakkal', 'perinthalmanna'.
  Existing rows will have NULL — that is safe and expected.

Run once with:
    python scripts/add_branch_to_contacts_and_sales.py
"""

import sys
from pathlib import Path
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import engine

CHECK_SQL = """
SELECT COUNT(*) FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME   = :table
  AND COLUMN_NAME  = 'branch'
"""

def add_branch(conn, table: str, after: str):
    """Add the branch ENUM column to `table` unless it already exists."""
    (count,) = conn.execute(text(CHECK_SQL), {"table": table}).fetchone()
    if count:
        print(f"{table}.branch already exists — skipping.")
        return
    conn.execute(text(
        f"ALTER TABLE {table} "
        f"ADD COLUMN branch ENUM('malappuram', 'kottakkal', 'perinthalmanna') NULL "
        f"AFTER {after}"
    ))
    print(f"{table}.branch added.")

with engine.begin() as conn:
    add_branch(conn, "contacts", "notes")
    add_branch(conn, "sales", "purchased_product")

print("Migration complete.")
