"""Fixture data shared by the pytest suite and the mobile end-to-end server."""
import models
import settings_catalog
from auth import get_password_hash
from models import Employee, LeadSource

PASSWORD = "Passw0rd!"

# Header the mobile app sends. Sales executives may only sign in from the mobile app, so tests sign in with it.
MOBILE_APP = {"x-client-type": "mobile"}

# One admin, one manager and three sales executives, so any test or live e2e run can log in as each
# role, and as a second and third staff member, without building its own fixtures first.
STAFF = [
    # key, name, role, phone
    ("admin", "Ada Admin", "admin", "9000000001"),
    ("manager", "Mani Manager", "manager", "9000000002"),
    ("anu", "Anu Sales", "sales_executive", "9000000003"),
    ("raj", "Raj Sales", "sales_executive", "9000000004"),
    ("sam", "Sam Sales", "sales_executive", "9000000007"),
]

LEAD_SOURCES = (
    ("Meta", "meta"),
    ("Website", "website"),
    ("Showroom", "showroom"),
    ("Offline", "offline"),
    ("Field Visit", "field_visit"),
)


def email_of(key: str) -> str:
    return f"{key}@example.com"


def seed(db) -> None:
    """Roles, one employee per key in STAFF, and the lead sources."""
    from models import Role
    from sqlmodel import select

    # Create roles if they don't exist, then map slug → id.
    role_slugs = {slug for _, _, slug, _ in STAFF}
    for slug in role_slugs:
        existing = db.scalar(select(Role).where(Role.slug == slug))
        if not existing:
            db.add(Role(slug=slug, name=slug.replace("_", " ").title()))
    db.flush()

    # Build slug → id map.
    role_id_map = {
        r.slug: r.id
        for r in db.scalars(select(Role)).all()
    }

    digest = get_password_hash(PASSWORD)
    for key, name, role_slug, phone in STAFF:
        db.add(Employee(
            name=name,
            email=email_of(key),
            password_hash=digest,
            role_id=role_id_map[role_slug],
            phone=phone,
        ))
    for name, slug in LEAD_SOURCES:
        db.add(LeadSource(name=name, slug=slug))
    db.commit()
    settings_catalog.seed_defaults(db)  # dropdown options, pipeline stages, company name
