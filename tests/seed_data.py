"""Fixture data shared by the pytest suite and the mobile end-to-end server."""
import models
import settings_catalog
from auth import get_password_hash
from models import Employee, LeadSource

PASSWORD = "Passw0rd!"

STAFF = [
    # key, name, role, phone
    ("admin", "Ada Admin", "admin", "9000000001"),
    ("manager", "Mani Manager", "manager", "9000000002"),
    ("anu", "Anu Sales", "sales_executive", "9000000003"),
    ("raj", "Raj Field", "field_executive", "9000000004"),
]

LEAD_SOURCES = (
    ("Meta", "meta"),
    ("Website", "website"),
    ("Showroom", "showroom"),
    ("Offline", "offline"),
)


def email_of(key: str) -> str:
    return f"{key}@example.com"


def seed(db) -> None:
    """Built-in roles, one employee per key in STAFF, and the lead sources."""
    models.seed_system_roles(db)
    digest = get_password_hash(PASSWORD)
    for key, name, role, phone in STAFF:
        db.add(Employee(name=name, email=email_of(key), password_hash=digest, role=role, phone=phone))
    for name, slug in LEAD_SOURCES:
        db.add(LeadSource(name=name, slug=slug))
    db.commit()
    settings_catalog.seed_defaults(db)  # dropdown options, pipeline stages, company name
