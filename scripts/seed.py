"""
Seed script for default data: admin users, lead sources, etc.
Run with: python scripts/seed.py (from project root)
"""

import sys
from pathlib import Path
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent.parent))

from passlib.context import CryptContext
from database import SessionLocal, engine
import models  # noqa: F401
from models import LeadSource, Employee, StageType

# Use argon2 for password hashing (more reliable than bcrypt)
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

DEFAULT_ADMIN_USERS = [
    {
        "name": "Admin",
        "email": "admin@ogera.com",
        "password": "admin",
        "role": "admin",
        "is_field_staff": False,
        "phone": None,
        "is_active": True,
    },
]

DEFAULT_LEAD_SOURCES = [
    {"name": "Meta", "slug": "meta"},
    {"name": "Website", "slug": "website"},
    {"name": "Showroom", "slug": "showroom"},
    {"name": "Google My Business", "slug": "gmb"},
    {"name": "WhatsApp", "slug": "whatsapp"},
    {"name": "Offline", "slug": "offline"},
]

DEFAULT_STAGE_TYPES = [
    {"name": "New Lead", "slug": "new_lead", "description": "Just entered. Not yet contacted. 24h clock ticking.", "order": 1},
    {"name": "First Call", "slug": "first_call", "description": "First call attempted. Waiting to reach them.", "order": 2},
    {"name": "Connected", "slug": "connected", "description": "Spoke to them. Qualify the need and decide the next step.", "order": 3},
    {"name": "Aware", "slug": "aware", "description": "Contacted. Needs education before they're ready to hear a price.", "order": 4},
    {"name": "Interested", "slug": "interested", "description": "Understands the product. Ready for demo or detailed info.", "order": 5},
    {"name": "Quoted", "slug": "quoted", "description": "Quote sent. Now the 3-day follow-up clock starts.", "order": 6},
    {"name": "Negotiating", "slug": "negotiating", "description": "Active discussion on price, timing, or terms.", "order": 7},
    {"name": "Won", "slug": "won", "description": "Deal closed. Order being created. Payment tracking begins.", "order": 8},
    {"name": "Lost", "slug": "lost", "description": "Did not buy. Reason captured. Re-engage in 60 days.", "order": 9},
    {"name": "Repeat Customer", "slug": "repeat_customer", "description": "Bought again or came back for another purchase.", "order": 10},
]


def hash_password(password: str) -> str:
    """Hash a password using bcrypt (truncate to 72 bytes for bcrypt limit)."""
    # Bcrypt has a 72-byte limit on password length
    truncated = password[:72]
    return pwd_context.hash(truncated)


def seed_admin_users():
    """Seed default admin users if they don't exist."""
    db = SessionLocal()
    try:
        for user_data in DEFAULT_ADMIN_USERS:
            existing = db.query(Employee).filter_by(email=user_data["email"]).first()
            if not existing:
                try:
                    user_data_copy = user_data.copy()
                    password = user_data_copy.pop("password")
                    print(f"  Hashing password (length: {len(password)} bytes)...")
                    user_data_copy["password_hash"] = hash_password(password)
                    admin = Employee(**user_data_copy)
                    db.add(admin)
                    print(f"✓ Added admin: {user_data['email']} (password: {user_data['password']})")
                except Exception as inner_e:
                    print(f"✗ Error hashing password for {user_data['email']}: {inner_e}")
                    raise
            else:
                print(f"- Skipped (exists): {user_data['email']}")
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"✗ Error seeding admin users: {e}")
    finally:
        db.close()


def seed_lead_sources():
    """Seed default lead sources if they don't exist."""
    db = SessionLocal()
    try:
        for source_data in DEFAULT_LEAD_SOURCES:
            existing = db.query(LeadSource).filter_by(slug=source_data["slug"]).first()
            if not existing:
                lead_source = LeadSource(**source_data, is_active=True)
                db.add(lead_source)
                print(f"✓ Added lead source: {source_data['name']}")
            else:
                print(f"- Skipped (exists): {source_data['name']}")
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"✗ Error seeding lead sources: {e}")
    finally:
        db.close()


def seed_stage_types():
    """Seed default stage types (available lead stages)."""
    db = SessionLocal()
    try:
        for stage_data in DEFAULT_STAGE_TYPES:
            existing = db.query(StageType).filter_by(slug=stage_data["slug"]).first()
            if not existing:
                stage_type = StageType(**stage_data, is_active=True)
                db.add(stage_type)
                print(f"✓ Added stage type: {stage_data['name']}")
            else:
                print(f"- Skipped (exists): {stage_data['name']}")
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"✗ Error seeding stage types: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    engine.echo = False

    # Read and execute SQL file to create tables
    try:
        sql_file = Path(__file__).parent / "create_tables.sql"
        with open(sql_file, "r") as f:
            sql = f.read()

        with engine.connect() as conn:
            # Execute all statements
            for statement in sql.split(";"):
                if statement.strip():
                    conn.execute(text(statement))
            conn.commit()

        print("✓ Database tables created/verified.\n")
    except Exception as e:
        print(f"✗ Error creating tables: {e}\n")

    print("Seeding admin users...")
    seed_admin_users()

    print("\nSeeding lead sources...")
    seed_lead_sources()

    print("\nSeeding stage types...")
    seed_stage_types()

    print("\n✓ All data seeded successfully!")
