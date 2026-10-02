"""
Clean up existing tables to recreate with updated schema.
Run with: python scripts/cleanup.py
"""

import sys
from pathlib import Path
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import engine

engine.echo = False

with engine.connect() as conn:
    conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
    conn.execute(text("DROP TABLE IF EXISTS lead_stages"))
    conn.execute(text("DROP TABLE IF EXISTS contacts"))
    conn.execute(text("DROP TABLE IF EXISTS consumer_profiles"))
    conn.execute(text("DROP TABLE IF EXISTS partner_profiles"))
    conn.execute(text("DROP TABLE IF EXISTS employees"))
    conn.execute(text("DROP TABLE IF EXISTS lead_sources"))
    conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    conn.commit()

print("✓ All tables dropped successfully")
