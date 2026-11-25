"""Fixture data shared by the pytest suite and the mobile end-to-end server."""
import models
import settings_catalog
from auth import get_password_hash
from models import Employee, LeadSource, EmployeeLookup, Tenant

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
    """Roles, one employee per key in STAFF, lead sources, and multi-tenant lookup tables."""
    from models import Role
    from sqlmodel import select

    # Create a single test tenant. The schema_name must pass _validate_schema_name
    # (lowercase letters/digits/underscores, starts with a letter).
    tenant = Tenant(id=1, name="Test Company", schema_name="test", status="active")
    db.add(tenant)
    db.flush()

    # Create roles if they don't exist, then map slug → id.
    role_slugs = {slug for _, _, slug, _ in STAFF}
    for slug in role_slugs:
        existing = db.scalar(select(Role).where(Role.slug == slug))
        if not existing:
            db.add(Role(slug=slug, name=slug.replace("_", " ").title()))
    db.flush()

    # Production gets these from scripts/migrations/002 and 003: admin has every module,
    # manager is a head role (sees all records) with WhatsApp integrations read and update.
    # Without them, every permission-checked route answers 403, admin included.
    for slug, perms, see_all in (
        ("admin", {m: "crud" for m in ("leads", "contacts", "employees", "settings", "team",
                                        "integrations", "lead_statuses", "company")}, True),
        ("manager", {"leads": "crud", "contacts": "crud", "employees": "crud", "settings": "r",
                     "team": "", "integrations": "ru", "lead_statuses": "", "company": ""}, True),
    ):
        role = db.scalar(select(Role).where(Role.slug == slug))
        if role is not None:
            role.permissions = perms
            role.see_all_records = see_all
    db.flush()

    # The three branch offices the web offers; employees point at them by branch_id.
    from models.branch import Branch
    for code, name in (("malappuram", "Malappuram"), ("perinthalmanna", "Perinthalmanna"), ("kottakkal", "Kottakkal")):
        if db.scalar(select(Branch).where(Branch.code == code)) is None:
            db.add(Branch(code=code, name=name, is_active=True))
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
        # employee_tenant_map lets login and password-reset look up the right tenant schema.
        db.add(EmployeeLookup(email=email_of(key), tenant_id=1))
    for name, slug in LEAD_SOURCES:
        db.add(LeadSource(name=name, slug=slug))
    db.commit()
    settings_catalog.seed_defaults(db)  # dropdown options, pipeline stages, company name
