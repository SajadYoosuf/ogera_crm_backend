#!/usr/bin/env python3
"""
seed_demo.py

Bootstrap a demo tenant (nexus_demo) with realistic 3-month data:
  - 6 employees: admin, manager, 3 sales execs, 1 tele caller
  - 1 000 leads spread over Sep–Nov 2026, all statuses
  - 335 contacts split across consumer / partner / dealer pipelines
  - Per-stage minimum contact counts so every Kanban column is populated
  - 1 200 interactions (800 activity logs + 400 tasks, with outcomes)
  - 500 record logs (conversion + assignment events for dashboard trend charts)

Usage:
    python scripts/seed_demo.py                 # insert with defaults
    python scripts/seed_demo.py --dry-run       # print counts, write nothing
    python scripts/seed_demo.py --reset         # drop & re-create nexus_demo first
    python scripts/seed_demo.py --schema acme   # use a different schema name

Run from inside ogera_crm_backend-leads/ so the .env file is found.
NEVER run this against the ventino schema or any production schema.
"""

import argparse
import datetime
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from passlib.context import CryptContext
from sqlalchemy import text

from database import engine

# ── Configuration ─────────────────────────────────────────────────────────────

DEFAULT_SCHEMA   = "nexus_demo"
COMPANY_NAME     = "Nexus Water Solutions"
ADMIN_EMAIL      = "admin@nexusdemo.com"
MANAGER_EMAIL    = "manager@nexusdemo.com"
SALES1_EMAIL     = "sales1@nexusdemo.com"
SALES2_EMAIL     = "sales2@nexusdemo.com"
SALES3_EMAIL     = "sales3@nexusdemo.com"
TELE_EMAIL       = "tele@nexusdemo.com"
DEMO_PASSWORD    = "Demo@2024"

SEED = 42        # fixed random seed → reproducible data
START_DATE       = datetime.date(2026, 9, 1)
END_DATE         = datetime.date(2026, 11, 28)

# ── Password hashing ──────────────────────────────────────────────────────────

_pwd = CryptContext(schemes=["argon2"], deprecated="auto")

def _hash(pw: str) -> str:
    return _pwd.hash(pw[:72])

# ── Random helpers ────────────────────────────────────────────────────────────

RNG = random.Random(SEED)

def _rand_date(start: datetime.date, end: datetime.date) -> datetime.datetime:
    delta = (end - start).days
    day   = start + datetime.timedelta(days=RNG.randint(0, delta))
    hour  = RNG.randint(8, 19)
    minute = RNG.randint(0, 59)
    return datetime.datetime(day.year, day.month, day.day, hour, minute, 0)

def _weighted(choices: list, weights: list):
    return RNG.choices(choices, weights=weights, k=1)[0]

# ── Kerala data pools ─────────────────────────────────────────────────────────

FIRST_NAMES = [
    "Muhammed", "Rahul", "Anjali", "Priya", "Arun", "Sreeja", "Vishnu", "Deepa",
    "Suresh", "Nisha", "Rajesh", "Meera", "Arjun", "Pooja", "Ajith", "Lekha",
    "Sajan", "Asha", "Midhun", "Fathima", "Hassan", "Khalid", "Ravi", "Divya",
    "Shibu", "Jisha", "Abhijith", "Sindhu", "Nikhil", "Reshma", "Jithin", "Anitha",
    "Sreedev", "Bindhu", "Noufal", "Sulekha", "Bipin", "Liji", "Santhosh", "Saritha",
    "Vineeth", "Shyama", "Abhilash", "Renu", "Mithun", "Siji", "Anoop", "Vidya",
]
LAST_NAMES = [
    "Kumar", "Nair", "Pillai", "Menon", "Varma", "Babu", "Krishnan", "Sharma",
    "Rajan", "Mohan", "Hamza", "Ali", "Hassan", "Khalid", "Fathima", "Rasheed",
    "Salam", "Nasar", "Thomas", "George", "Mathew", "Joseph", "Peter", "Paulose",
    "Sunil", "Ramesh", "Girish", "Prakash", "Vijayan", "Mohanan",
]
DISTRICTS = [
    "Malappuram", "Kozhikode", "Palakkad", "Thrissur", "Ernakulam",
    "Thiruvananthapuram", "Kollam", "Alappuzha", "Kottayam", "Wayanad",
    "Kannur", "Kasaragod", "Pathanamthitta", "Idukki",
]
PARTNER_TYPES  = ["Plumber", "Contractor", "Builder", "Interior Designer", "Electrician", "Hardware Shop"]
DEALER_NAMES   = [
    "Aqua Traders", "Pure Water Solutions", "AquaFresh Dealers", "Water Tech Kerala",
    "BlueDrop Enterprises", "ClearFlow Distributors", "Kerala Water Works",
    "Hydro Solutions", "AquaPlus Retail", "Pristine Water Dealers",
]
LOSS_REASONS = ["price", "competitor", "not_interested", "no_budget", "unreachable"]
NOTES_POOL = [
    "Interested in 10-litre purifier.", "Called twice, no answer.",
    "Requested product demo at site.", "Price was the main concern.",
    "Referral from existing customer.", "Saw our ad on Facebook.",
    "Builder project — bulk enquiry.", "Wanted installation within 2 weeks.",
    "Comparing with competitor product.", "Follow up after Diwali.",
    "Site not ready yet — call in 30 days.", "Very interested, needs approval.",
    "Plumber referred this lead.", "Needs quotation for 3 units.",
    "Office installation project.", "Residential apartment block.",
]

def _phone() -> tuple[str, str]:
    """Return (phone, phone_key) — 10-digit Indian mobile."""
    # Random 9-digit suffix after a leading '9'
    suffix = "".join(str(RNG.randint(0, 9)) for _ in range(9))
    num = "9" + suffix
    return "+91" + num, num[-10:]

def _name() -> str:
    return f"{RNG.choice(FIRST_NAMES)} {RNG.choice(LAST_NAMES)}"

# ── Schema DDL ────────────────────────────────────────────────────────────────

def _schema_ddl(schema: str) -> list[str]:
    """Return ordered CREATE TABLE statements for the demo schema."""
    return [
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        f"SET search_path TO {schema}",

        """CREATE TABLE IF NOT EXISTS roles (
            id          SERIAL       PRIMARY KEY,
            slug        VARCHAR(40)  NOT NULL UNIQUE,
            name        VARCHAR(80)  NOT NULL UNIQUE,
            is_active   SMALLINT     NOT NULL DEFAULT 1,
            parent_id   INT          REFERENCES roles(id) ON DELETE SET NULL,
            is_system   SMALLINT     NOT NULL DEFAULT 0,
            description VARCHAR(255),
            see_all_records BOOLEAN  NOT NULL DEFAULT false,
            permissions JSON         NOT NULL DEFAULT '{}',
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )""",

        """CREATE TABLE IF NOT EXISTS branches (
            id         BIGSERIAL    PRIMARY KEY,
            name       VARCHAR(100) NOT NULL,
            code       VARCHAR(30)  NOT NULL UNIQUE,
            is_active  BOOLEAN      DEFAULT true,
            created_at TIMESTAMPTZ  DEFAULT NOW()
        )""",

        """CREATE TABLE IF NOT EXISTS employees (
            id            BIGSERIAL    PRIMARY KEY,
            name          VARCHAR(120) NOT NULL,
            email         VARCHAR(180) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            role_id       INT          NOT NULL REFERENCES roles(id),
            phone         VARCHAR(20)  UNIQUE,
            is_active     SMALLINT     NOT NULL DEFAULT 1,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            deleted_at    TIMESTAMPTZ,
            fcm_token     VARCHAR(512),
            branch_id     BIGINT       REFERENCES branches(id)
        )""",

        """CREATE TABLE IF NOT EXISTS password_reset_codes (
            id          BIGSERIAL   PRIMARY KEY,
            employee_id BIGINT      NOT NULL REFERENCES employees(id),
            code_hash   VARCHAR(64) NOT NULL,
            attempts    SMALLINT    NOT NULL DEFAULT 0,
            expires_at  TIMESTAMPTZ NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            consumed_at TIMESTAMPTZ
        )""",

        """CREATE TABLE IF NOT EXISTS lead_sources (
            id         SERIAL      PRIMARY KEY,
            name       VARCHAR(60) NOT NULL UNIQUE,
            slug       VARCHAR(40) NOT NULL UNIQUE,
            is_active  SMALLINT    NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",

        """CREATE TABLE IF NOT EXISTS pipeline_stages (
            id           SERIAL      PRIMARY KEY,
            name         VARCHAR(60) NOT NULL,
            slug         VARCHAR(40) NOT NULL,
            contact_type VARCHAR(20) NOT NULL,
            description  VARCHAR(255),
            "order"      SMALLINT    NOT NULL,
            is_active    SMALLINT    NOT NULL DEFAULT 1,
            UNIQUE (slug, contact_type)
        )""",

        """CREATE TABLE IF NOT EXISTS lead_statuses (
            id         SERIAL      PRIMARY KEY,
            slug       VARCHAR(40) NOT NULL UNIQUE,
            name       VARCHAR(80) NOT NULL,
            sort_order SMALLINT    NOT NULL DEFAULT 0,
            is_active  SMALLINT    NOT NULL DEFAULT 1
        )""",

        """CREATE TABLE IF NOT EXISTS leads (
            id              BIGSERIAL   PRIMARY KEY,
            first_name      VARCHAR(200),
            last_name       VARCHAR(100),
            phone           VARCHAR(20),
            phone_key       VARCHAR(10),
            email           VARCHAR(180),
            source_id       BIGINT,
            external_ref    VARCHAR(200),
            raw_payload     JSONB,
            status          VARCHAR(30)  DEFAULT 'new',
            notes           TEXT,
            address         TEXT,
            tags            TEXT[],
            score           SMALLINT     DEFAULT 0,
            whatsapp_sent   BOOLEAN      DEFAULT false,
            whatsapp_sent_at TIMESTAMPTZ,
            created_by      BIGINT       REFERENCES employees(id) ON DELETE SET NULL,
            assigned_to     BIGINT,
            deleted_at      TIMESTAMPTZ,
            converted_at    TIMESTAMPTZ,
            contact_id      BIGINT,
            branch_id       BIGINT       REFERENCES branches(id) ON DELETE SET NULL,
            created_at      TIMESTAMPTZ  DEFAULT NOW(),
            updated_at      TIMESTAMPTZ  DEFAULT NOW()
        )""",

        """CREATE TABLE IF NOT EXISTS contacts (
            id              BIGSERIAL   PRIMARY KEY,
            contact_type    VARCHAR(10) DEFAULT 'consumer',
            name            VARCHAR(150),
            phone           VARCHAR(20) NOT NULL,
            phone_key       VARCHAR(10) NOT NULL DEFAULT '',
            phone_alt       VARCHAR(20),
            district        VARCHAR(80),
            address         VARCHAR(255),
            assigned_to     BIGINT      REFERENCES employees(id),
            created_by      BIGINT      REFERENCES employees(id),
            referred_by_id  BIGINT      REFERENCES contacts(id),
            branch_id       BIGINT      REFERENCES branches(id),
            lead_id         BIGINT      REFERENCES leads(id),
            lead_source_id  BIGINT      REFERENCES lead_sources(id),
            notes           TEXT,
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            updated_at      TIMESTAMPTZ DEFAULT NOW(),
            deleted_at      TIMESTAMPTZ
        )""",

        """CREATE TABLE IF NOT EXISTS consumer_profiles (
            id         BIGSERIAL PRIMARY KEY,
            contact_id BIGINT    NOT NULL REFERENCES contacts(id) ON DELETE CASCADE UNIQUE,
            data       JSONB     NOT NULL DEFAULT '{}'
        )""",

        """CREATE TABLE IF NOT EXISTS partner_profiles (
            id         BIGSERIAL PRIMARY KEY,
            contact_id BIGINT    NOT NULL REFERENCES contacts(id) ON DELETE CASCADE UNIQUE,
            data       JSONB     NOT NULL DEFAULT '{}'
        )""",

        """CREATE TABLE IF NOT EXISTS dealer_profiles (
            id                  BIGSERIAL     PRIMARY KEY,
            contact_id          BIGINT        NOT NULL REFERENCES contacts(id) UNIQUE,
            business_name       VARCHAR(120),
            gstin               VARCHAR(20),
            town_city           VARCHAR(80),
            pin_code            VARCHAR(10),
            dealer_type         VARCHAR(60),
            assigned_rep        BIGINT        REFERENCES employees(id),
            credit_limit        NUMERIC(12,2) DEFAULT 0,
            outstanding_balance NUMERIC(12,2) DEFAULT 0,
            data                JSONB         NOT NULL DEFAULT '{}'
        )""",

        """CREATE TABLE IF NOT EXISTS lead_stage_log (
            id           BIGSERIAL   PRIMARY KEY,
            contact_id   BIGINT      NOT NULL REFERENCES contacts(id),
            stage_id     INT         NOT NULL REFERENCES pipeline_stages(id),
            changed_by   BIGINT      NOT NULL REFERENCES employees(id),
            changed_at   TIMESTAMPTZ DEFAULT NOW(),
            loss_reason  VARCHAR(40),
            loss_notes   TEXT,
            awareness_note TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS interactions (
            id             BIGSERIAL   PRIMARY KEY,
            entity_type    VARCHAR(30) NOT NULL,
            entity_id      BIGINT      NOT NULL,
            kind           VARCHAR(10) NOT NULL,
            activity_type  VARCHAR(50),
            custom_label   VARCHAR(100),
            title          VARCHAR(255),
            notes          TEXT,
            done_by        BIGINT      REFERENCES employees(id) ON DELETE SET NULL,
            assigned_to    BIGINT      REFERENCES employees(id) ON DELETE SET NULL,
            created_by     BIGINT      REFERENCES employees(id) ON DELETE SET NULL,
            due_at         TIMESTAMPTZ,
            priority       VARCHAR(20) DEFAULT 'none',
            status         VARCHAR(20) DEFAULT 'todo',
            outcome        VARCHAR(50),
            parent_task_id BIGINT      REFERENCES interactions(id) ON DELETE SET NULL,
            logged_at      TIMESTAMPTZ,
            created_at     TIMESTAMPTZ DEFAULT NOW()
        )""",

        """CREATE TABLE IF NOT EXISTS record_logs (
            id          BIGSERIAL   PRIMARY KEY,
            entity_type VARCHAR(20) NOT NULL,
            entity_id   BIGINT      NOT NULL,
            action      VARCHAR(40) NOT NULL,
            actor_id    BIGINT      REFERENCES employees(id),
            old_value   TEXT,
            new_value   TEXT,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )""",

        # Indexes for performance
        "CREATE INDEX IF NOT EXISTS idx_leads_status    ON leads (status)",
        "CREATE INDEX IF NOT EXISTS idx_leads_assigned  ON leads (assigned_to)",
        "CREATE INDEX IF NOT EXISTS idx_leads_created   ON leads (created_at)",
        "CREATE INDEX IF NOT EXISTS idx_leads_phone_key ON leads (phone_key)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_type   ON contacts (contact_type)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_assigned ON contacts (assigned_to)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_phone_key ON contacts (phone_key)",
        "CREATE INDEX IF NOT EXISTS idx_lead_stage_contact ON lead_stage_log (contact_id)",
        "CREATE INDEX IF NOT EXISTS idx_interactions_entity ON interactions (entity_type, entity_id)",
        "CREATE INDEX IF NOT EXISTS idx_interactions_kind   ON interactions (kind)",
        "CREATE INDEX IF NOT EXISTS idx_record_logs_entity  ON record_logs (entity_type, entity_id, created_at)",
    ]

# ── Reference data ────────────────────────────────────────────────────────────

ROLES = [
    {"slug": "admin",           "name": "Admin",           "see_all": True,  "is_sys": 1},
    {"slug": "manager",         "name": "Manager",         "see_all": True,  "is_sys": 1},
    {"slug": "team_lead",       "name": "Team Lead",       "see_all": False, "is_sys": 1},
    {"slug": "sales_executive", "name": "Sales Executive", "see_all": False, "is_sys": 1},
    {"slug": "tele_caller",     "name": "Tele Caller",     "see_all": False, "is_sys": 1},
    {"slug": "field_executive", "name": "Field Executive", "see_all": False, "is_sys": 1},
    {"slug": "support",         "name": "Support",         "see_all": False, "is_sys": 1},
]

BRANCHES = [
    {"name": "Calicut",       "code": "calicut"},
    {"name": "Kozhikode",     "code": "kozhikode"},
    {"name": "Malappuram",    "code": "malappuram"},
]

LEAD_SOURCES = [
    {"name": "Meta Ads",   "slug": "meta"},
    {"name": "Website",    "slug": "website"},
    {"name": "Showroom",   "slug": "showroom"},
    {"name": "Google My Business", "slug": "gmb"},
    {"name": "WhatsApp",   "slug": "whatsapp"},
    {"name": "Offline",    "slug": "offline"},
]

LEAD_STATUSES = [
    {"slug": "new",           "name": "New",           "sort_order": 1},
    {"slug": "contacted",     "name": "Contacted",     "sort_order": 2},
    {"slug": "qualified",     "name": "Qualified",     "sort_order": 3},
    {"slug": "converted",     "name": "Converted",     "sort_order": 4},
    {"slug": "lost",          "name": "Lost",          "sort_order": 5},
    {"slug": "junk_lead",     "name": "Junk Lead",     "sort_order": 6},
    {"slug": "not_qualified", "name": "Not Qualified", "sort_order": 7},
]

# Pipeline stages per contact type — order matters for Kanban display
PIPELINE_STAGES = {
    "consumer": [
        {"name": "New Lead",         "slug": "new_lead",        "order": 1, "desc": "Just entered. Not yet contacted."},
        {"name": "First Call",       "slug": "first_call",      "order": 2, "desc": "First call attempted."},
        {"name": "Connected",        "slug": "connected",       "order": 3, "desc": "Spoke to them. Qualify the need."},
        {"name": "Aware",            "slug": "aware",           "order": 4, "desc": "Needs education before price talk."},
        {"name": "Interested",       "slug": "interested",      "order": 5, "desc": "Ready for demo or detailed info."},
        {"name": "Quoted",           "slug": "quoted",          "order": 6, "desc": "Quote sent."},
        {"name": "Negotiating",      "slug": "negotiating",     "order": 7, "desc": "Active discussion on price."},
        {"name": "Won",              "slug": "won",             "order": 8, "desc": "Deal closed."},
        {"name": "Lost",             "slug": "lost",            "order": 9, "desc": "Did not buy."},
        {"name": "Repeat Customer",  "slug": "repeat_customer", "order": 10, "desc": "Bought again."},
    ],
    "partner": [
        {"name": "New Lead",         "slug": "new_lead",        "order": 1, "desc": "New partner enquiry."},
        {"name": "First Contact",    "slug": "first_contact",   "order": 2, "desc": "First outreach done."},
        {"name": "Met",              "slug": "met",             "order": 3, "desc": "In-person or video meeting done."},
        {"name": "Active Partner",   "slug": "active_partner",  "order": 4, "desc": "Sending referrals regularly."},
        {"name": "Top Performer",    "slug": "top_performer",   "order": 5, "desc": "Top referral contributor."},
        {"name": "Inactive",         "slug": "inactive",        "order": 6, "desc": "No referrals in 60+ days."},
    ],
    "dealer": [
        {"name": "New Lead",         "slug": "new_lead",        "order": 1, "desc": "Enquiry received."},
        {"name": "Demo Done",        "slug": "demo_done",       "order": 2, "desc": "Product demo completed."},
        {"name": "Terms Sent",       "slug": "terms_sent",      "order": 3, "desc": "Commercial terms shared."},
        {"name": "Active Dealer",    "slug": "active_dealer",   "order": 4, "desc": "Signed on, ordering stock."},
        {"name": "Inactive Dealer",  "slug": "inactive_dealer", "order": 5, "desc": "No orders in 90 days."},
    ],
}

# Minimum contacts that MUST sit at each stage (current stage = last log entry)
STAGE_MINIMUMS = {
    "consumer": {
        "new_lead": 20, "first_call": 25, "connected": 30, "aware": 20,
        "interested": 35, "quoted": 25, "negotiating": 20, "won": 40,
        "lost": 15, "repeat_customer": 5,
    },
    "partner": {
        "new_lead": 10, "first_contact": 12, "met": 12,
        "active_partner": 20, "top_performer": 8, "inactive": 8,
    },
    "dealer": {
        "new_lead": 5, "demo_done": 7, "terms_sent": 6,
        "active_dealer": 9, "inactive_dealer": 3,
    },
}

# ── Employees definition ──────────────────────────────────────────────────────

def _employee_defs(role_ids: dict, branch_ids: list) -> list[dict]:
    pw = _hash(DEMO_PASSWORD)
    b = branch_ids
    return [
        {"name": "Admin User",    "email": ADMIN_EMAIL,   "password_hash": pw, "role": "admin",           "branch_idx": 0},
        {"name": "Sales Manager", "email": MANAGER_EMAIL, "password_hash": pw, "role": "manager",         "branch_idx": 0},
        {"name": "Ahmed Khalid",  "email": SALES1_EMAIL,  "password_hash": pw, "role": "sales_executive", "branch_idx": 0},
        {"name": "Priya Menon",   "email": SALES2_EMAIL,  "password_hash": pw, "role": "sales_executive", "branch_idx": 1},
        {"name": "Jithin Nair",   "email": SALES3_EMAIL,  "password_hash": pw, "role": "sales_executive", "branch_idx": 2},
        {"name": "Fathima Rasheed","email": TELE_EMAIL,   "password_hash": pw, "role": "tele_caller",     "branch_idx": 0},
    ]

# ── Insert helpers ────────────────────────────────────────────────────────────

def _exec(conn, sql: str, params=None):
    if params:
        conn.execute(text(sql), params)
    else:
        conn.execute(text(sql))

def _one(conn, sql: str, params=None):
    r = conn.execute(text(sql), params or {})
    return r.fetchone()

def _all(conn, sql: str, params=None):
    r = conn.execute(text(sql), params or {})
    return r.fetchall()

# ── Main seeder ───────────────────────────────────────────────────────────────

def seed(schema: str = DEFAULT_SCHEMA, dry_run: bool = False, reset: bool = False):
    print(f"\n{'[DRY RUN] ' if dry_run else ''}Seeding demo tenant → schema: {schema}")
    print(f"  Company : {COMPANY_NAME}")
    print(f"  Admin   : {ADMIN_EMAIL} / {DEMO_PASSWORD}")
    print(f"  Period  : {START_DATE} → {END_DATE}\n")

    if dry_run:
        _print_plan()
        return

    with engine.connect() as conn:
        # ── 0. Reset ───────────────────────────────────────────────────────────
        if reset:
            print(f"  Dropping schema {schema} …")
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            conn.execute(text(
                "DELETE FROM public.employee_tenant_map WHERE tenant_id = "
                "(SELECT id FROM public.tenants WHERE schema_name = :s)"
            ), {"s": schema})
            conn.execute(text(
                "DELETE FROM public.tenants WHERE schema_name = :s"
            ), {"s": schema})
            conn.commit()

        # ── 1. Schema + tables ────────────────────────────────────────────────
        print("  Creating schema and tables …")
        for stmt in _schema_ddl(schema):
            conn.execute(text(stmt))
        conn.commit()

        # ── 2. Public tenant row ──────────────────────────────────────────────
        existing_tenant = _one(conn,
            "SELECT id FROM public.tenants WHERE schema_name = :s", {"s": schema})
        if existing_tenant:
            tenant_id = existing_tenant[0]
            print(f"  Tenant row exists (id={tenant_id}), skipping insert.")
        else:
            conn.execute(text(
                "INSERT INTO public.tenants (name, schema_name) VALUES (:n, :s)"
            ), {"n": COMPANY_NAME, "s": schema})
            tenant_id = _one(conn,
                "SELECT id FROM public.tenants WHERE schema_name = :s", {"s": schema})[0]
            conn.commit()
            print(f"  Tenant created (id={tenant_id}).")

        # Point all subsequent queries at the demo schema
        conn.execute(text(f"SET search_path TO {schema}"))
        conn.commit()

        # ── 3. Roles ──────────────────────────────────────────────────────────
        print("  Seeding roles …")
        for r in ROLES:
            conn.execute(text("""
                INSERT INTO roles (slug, name, see_all_records, is_system)
                VALUES (:slug, :name, :see, :sys)
                ON CONFLICT (slug) DO NOTHING
            """), {"slug": r["slug"], "name": r["name"],
                   "see": r["see_all"], "sys": r["is_sys"]})
        conn.commit()
        role_ids = {row[0]: row[1] for row in _all(conn, "SELECT slug, id FROM roles")}

        # ── 4. Branches ───────────────────────────────────────────────────────
        print("  Seeding branches …")
        for b in BRANCHES:
            conn.execute(text("""
                INSERT INTO branches (name, code)
                VALUES (:name, :code)
                ON CONFLICT (code) DO NOTHING
            """), b)
        conn.commit()
        branch_rows = _all(conn, "SELECT id, code FROM branches ORDER BY id")
        branch_ids  = [r[0] for r in branch_rows]
        branch_by_code = {r[1]: r[0] for r in branch_rows}

        # ── 5. Lead sources ───────────────────────────────────────────────────
        print("  Seeding lead sources …")
        for s in LEAD_SOURCES:
            conn.execute(text("""
                INSERT INTO lead_sources (name, slug)
                VALUES (:name, :slug)
                ON CONFLICT (slug) DO NOTHING
            """), s)
        conn.commit()
        source_rows = _all(conn, "SELECT id, slug FROM lead_sources")
        source_ids  = [r[0] for r in source_rows]
        source_weights = [25, 20, 15, 15, 15, 10]  # meta, website, showroom, gmb, wa, offline

        # ── 6. Lead statuses ──────────────────────────────────────────────────
        print("  Seeding lead statuses …")
        for ls in LEAD_STATUSES:
            conn.execute(text("""
                INSERT INTO lead_statuses (slug, name, sort_order)
                VALUES (:slug, :name, :sort_order)
                ON CONFLICT (slug) DO NOTHING
            """), ls)
        conn.commit()

        # ── 7. Pipeline stages ────────────────────────────────────────────────
        print("  Seeding pipeline stages …")
        for ctype, stages in PIPELINE_STAGES.items():
            for s in stages:
                conn.execute(text("""
                    INSERT INTO pipeline_stages (name, slug, contact_type, description, "order")
                    VALUES (:name, :slug, :ctype, :desc, :ord)
                    ON CONFLICT (slug, contact_type) DO NOTHING
                """), {"name": s["name"], "slug": s["slug"], "ctype": ctype,
                       "desc": s["desc"], "ord": s["order"]})
        conn.commit()
        # Build {contact_type: {slug: id}}
        stage_rows = _all(conn, 'SELECT id, slug, contact_type FROM pipeline_stages ORDER BY "order"')
        stages_by_type: dict[str, dict[str, int]] = {}
        for sid, slug, ctype in stage_rows:
            stages_by_type.setdefault(ctype, {})[slug] = sid

        # ── 8. Employees ──────────────────────────────────────────────────────
        print("  Seeding employees …")
        emp_defs = _employee_defs(role_ids, branch_ids)
        emp_ids: dict[str, int] = {}
        for e in emp_defs:
            existing = _one(conn, "SELECT id FROM employees WHERE email = :email", {"email": e["email"]})
            if existing:
                emp_ids[e["email"]] = existing[0]
                print(f"    skip (exists): {e['email']}")
                continue
            conn.execute(text("""
                INSERT INTO employees (name, email, password_hash, role_id, branch_id)
                VALUES (:name, :email, :pw, :role_id, :branch_id)
            """), {
                "name": e["name"], "email": e["email"], "pw": e["password_hash"],
                "role_id": role_ids[e["role"]],
                "branch_id": branch_ids[e["branch_idx"]],
            })
            emp_ids[e["email"]] = _one(conn,
                "SELECT id FROM employees WHERE email = :email", {"email": e["email"]})[0]
            # Register in public lookup
            conn.execute(text("""
                INSERT INTO public.employee_tenant_map (email, tenant_id)
                VALUES (:email, :tid)
                ON CONFLICT (email) DO NOTHING
            """), {"email": e["email"], "tid": tenant_id})
            print(f"    created: {e['email']}")
        conn.commit()

        admin_id   = emp_ids[ADMIN_EMAIL]
        manager_id = emp_ids[MANAGER_EMAIL]
        sales_ids  = [emp_ids[SALES1_EMAIL], emp_ids[SALES2_EMAIL], emp_ids[SALES3_EMAIL]]
        tele_id    = emp_ids[TELE_EMAIL]
        all_staff  = sales_ids + [tele_id]

        # ── 9. Leads (1 000) ──────────────────────────────────────────────────
        print("  Generating 1 000 leads …")
        lead_status_dist = [
            ("new",           140),
            ("contacted",     170),
            ("qualified",     210),
            ("converted",     250),
            ("lost",          150),
            ("junk_lead",      50),
            ("not_qualified",  30),
        ]
        lead_rows_flat: list[tuple[str, int]] = []  # (status, lead_db_id)

        lead_batch = []
        for status, count in lead_status_dist:
            for _ in range(count):
                fname = RNG.choice(FIRST_NAMES)
                lname = RNG.choice(LAST_NAMES)
                phone, pkey = _phone()
                src_id = _weighted(source_ids, source_weights)
                assignee = RNG.choice(sales_ids) if RNG.random() > 0.05 else None
                branch_id = branch_by_code[RNG.choice([b["code"] for b in BRANCHES])]
                score_map = {
                    "new": (10, 40), "contacted": (20, 50), "qualified": (40, 70),
                    "converted": (60, 100), "lost": (5, 35),
                    "junk_lead": (1, 20), "not_qualified": (5, 30),
                }
                lo, hi = score_map[status]
                score = RNG.randint(lo, hi)
                created = _rand_date(START_DATE, END_DATE)
                converted_at = None
                if status == "converted":
                    conv_days = RNG.randint(1, 20)
                    converted_at = created + datetime.timedelta(days=conv_days)
                    if converted_at > datetime.datetime(2026, 11, 28, 23, 59):
                        converted_at = created + datetime.timedelta(hours=RNG.randint(2, 48))
                lead_batch.append({
                    "fname": fname, "lname": lname, "phone": phone, "pkey": pkey,
                    "src": src_id, "status": status, "score": score,
                    "assignee": assignee, "branch_id": branch_id,
                    "created_by": RNG.choice(all_staff),
                    "notes": RNG.choice(NOTES_POOL),
                    "address": RNG.choice(DISTRICTS),
                    "created": created,
                    "converted_at": converted_at,
                })

        RNG.shuffle(lead_batch)

        # Insert leads in one pass and collect ids
        lead_ids_by_status: dict[str, list[int]] = {s: [] for s, _ in lead_status_dist}
        for lb in lead_batch:
            conn.execute(text("""
                INSERT INTO leads
                    (first_name, last_name, phone, phone_key, source_id, status,
                     score, assigned_to, branch_id, created_by, notes, address,
                     whatsapp_sent, whatsapp_sent_at,
                     created_at, updated_at, converted_at)
                VALUES
                    (:fname, :lname, :phone, :pkey, :src, :status,
                     :score, :assignee, :branch_id, :created_by, :notes, :address,
                     true, NOW(),
                     :created, :created, :converted_at)
            """), lb)

        conn.commit()

        # Fetch inserted lead ids grouped by status
        all_leads = _all(conn, "SELECT id, status FROM leads ORDER BY id")
        for lid, lstatus in all_leads:
            if lstatus in lead_ids_by_status:
                lead_ids_by_status[lstatus].append(lid)

        converted_lead_ids = lead_ids_by_status["converted"]
        print(f"    {len(all_leads)} leads inserted.")

        # ── 10. Contacts (335 total) ──────────────────────────────────────────
        # 250 consumer (from converted leads + extras), 70 partner, 30 dealer (25 extra)
        print("  Generating contacts …")

        contact_ids: dict[str, list[int]] = {"consumer": [], "partner": [], "dealer": []}

        # 10a. Consumer contacts — first 250 from converted leads
        CONSUMER_COUNT = 250
        PARTNER_COUNT  = 70
        DEALER_COUNT   = 30

        def _insert_contact(ctype: str, name: str, phone: str, pkey: str,
                            assignee, branch_id, district: str,
                            lead_id=None, source_id=None,
                            created: datetime.datetime = None) -> int:
            if created is None:
                created = _rand_date(START_DATE, END_DATE)
            conn.execute(text("""
                INSERT INTO contacts
                    (contact_type, name, phone, phone_key, district, assigned_to,
                     created_by, branch_id, lead_id, lead_source_id, created_at, updated_at)
                VALUES
                    (:ctype, :name, :phone, :pkey, :district, :assignee,
                     :created_by, :branch_id, :lead_id, :source_id, :created, :created)
            """), {
                "ctype": ctype, "name": name, "phone": phone, "pkey": pkey,
                "district": district, "assignee": assignee,
                "created_by": RNG.choice(all_staff),
                "branch_id": branch_id,
                "lead_id": lead_id, "source_id": source_id,
                "created": created,
            })
            return _one(conn, "SELECT lastval()")[0]

        # Consumer contacts from converted leads
        for lead_id in converted_lead_ids[:CONSUMER_COUNT]:
            lrow = _one(conn, "SELECT first_name, last_name, phone, phone_key, source_id, assigned_to, branch_id, created_at FROM leads WHERE id = :id", {"id": lead_id})
            cname = f"{lrow[0] or ''} {lrow[1] or ''}".strip() or _name()
            cid = _insert_contact(
                "consumer", cname, lrow[2] or _phone()[0],
                lrow[3] or _phone()[1],
                lrow[5] or RNG.choice(sales_ids),
                lrow[6] or RNG.choice(branch_ids),
                RNG.choice(DISTRICTS),
                lead_id=lead_id, source_id=lrow[4],
                created=lrow[7] + datetime.timedelta(days=RNG.randint(1, 3)),
            )
            contact_ids["consumer"].append(cid)
            # Update lead.contact_id
            conn.execute(text("UPDATE leads SET contact_id = :cid WHERE id = :lid"),
                         {"cid": cid, "lid": lead_id})
            # Consumer profile
            house_stages = ["planning", "under_construction", "occupied", "renovation"]
            tank_types   = ["plastic", "sintex", "stainless", "concrete"]
            conn.execute(text("""
                INSERT INTO consumer_profiles (contact_id, data)
                VALUES (:cid, :data)
            """), {"cid": cid, "data": json.dumps({
                "house_stage": RNG.choice(house_stages),
                "tank_type":   RNG.choice(tank_types),
                "tank_age":    RNG.randint(1, 15),
            })})

        # Partner contacts (direct, not from converted leads)
        for _ in range(PARTNER_COUNT):
            phone, pkey = _phone()
            cid = _insert_contact(
                "partner", _name(), phone, pkey,
                RNG.choice(sales_ids), RNG.choice(branch_ids),
                RNG.choice(DISTRICTS),
            )
            contact_ids["partner"].append(cid)
            conn.execute(text("""
                INSERT INTO partner_profiles (contact_id, data)
                VALUES (:cid, :data)
            """), {"cid": cid, "data": json.dumps({
                "partner_type": RNG.choice(PARTNER_TYPES),
                "work_area": RNG.choice(DISTRICTS),
            })})

        # Dealer contacts
        for i in range(DEALER_COUNT):
            phone, pkey = _phone()
            bname = RNG.choice(DEALER_NAMES)
            cid = _insert_contact(
                "dealer", bname, phone, pkey,
                RNG.choice(sales_ids), RNG.choice(branch_ids),
                RNG.choice(DISTRICTS),
            )
            contact_ids["dealer"].append(cid)
            conn.execute(text("""
                INSERT INTO dealer_profiles
                    (contact_id, business_name, town_city, dealer_type, assigned_rep)
                VALUES (:cid, :bname, :town, :dtype, :rep)
            """), {
                "cid": cid, "bname": bname,
                "town": RNG.choice(DISTRICTS),
                "dtype": RNG.choice(["Regional", "City", "Exclusive"]),
                "rep": RNG.choice(sales_ids),
            })

        conn.commit()
        total_contacts = sum(len(v) for v in contact_ids.values())
        print(f"    {total_contacts} contacts inserted "
              f"(consumer={len(contact_ids['consumer'])}, "
              f"partner={len(contact_ids['partner'])}, "
              f"dealer={len(contact_ids['dealer'])}).")

        # ── 11. Pipeline stage history ────────────────────────────────────────
        # Strategy: assign each contact a CURRENT stage that meets the per-stage
        # minimum. Then build a history of 2–5 prior stages leading to it.
        print("  Generating pipeline stage history …")

        # For each contact type, allocate current stages meeting minimums
        current_stage_alloc: dict[int, int] = {}  # contact_id → stage_id

        for ctype, cids in contact_ids.items():
            type_stages = list(stages_by_type.get(ctype, {}).items())  # [(slug, id)]
            mins = STAGE_MINIMUMS.get(ctype, {})
            # Sort stages by their order (they come out ordered from the query)
            stage_slugs = [s["slug"] for s in PIPELINE_STAGES[ctype]]
            ordered_stages = [(slug, stages_by_type[ctype][slug]) for slug in stage_slugs
                              if slug in stages_by_type.get(ctype, {})]

            # Build a pool: for each stage, fill up to its minimum first
            allocation: list[tuple[str, int]] = []
            for slug, sid in ordered_stages:
                min_count = mins.get(slug, 0)
                allocation.extend([(slug, sid)] * min_count)

            # Fill remaining contacts with weighted distribution
            remaining = max(0, len(cids) - len(allocation))
            if remaining > 0:
                weights = [len(cids) // len(ordered_stages) + 1] * len(ordered_stages)
                # Skew toward earlier-middle stages (more realistic)
                for i in range(len(weights)):
                    if i < len(weights) // 2:
                        weights[i] = max(1, weights[i] + 2)
                for _ in range(remaining):
                    slug, sid = _weighted(ordered_stages, weights)
                    allocation.append((slug, sid))

            # Shuffle and assign to contacts
            RNG.shuffle(allocation)
            for cid, (slug, sid) in zip(cids, allocation):
                current_stage_alloc[cid] = sid

        # Insert stage log rows for each contact
        stage_log_count = 0
        for ctype, cids in contact_ids.items():
            type_stages_ordered = [
                (s["slug"], stages_by_type[ctype][s["slug"]])
                for s in PIPELINE_STAGES[ctype]
                if s["slug"] in stages_by_type.get(ctype, {})
            ]
            stage_slug_to_idx = {slug: i for i, (slug, _) in enumerate(type_stages_ordered)}

            for cid in cids:
                target_sid = current_stage_alloc[cid]
                # Find the index of the target stage
                target_slug = next(
                    slug for slug, sid in stages_by_type[ctype].items() if sid == target_sid
                )
                target_idx = stage_slug_to_idx.get(target_slug, 0)

                # Build a history from stage 0 up to target_idx
                history_depth = min(target_idx + 1, RNG.randint(2, 5))
                history_start = max(0, target_idx - history_depth + 1)
                history_stages = type_stages_ordered[history_start:target_idx + 1]

                base_time = _rand_date(START_DATE, END_DATE - datetime.timedelta(days=20))
                for step, (slug, sid) in enumerate(history_stages):
                    changed_at = base_time + datetime.timedelta(days=step * RNG.randint(1, 4))
                    actor = RNG.choice(sales_ids + [manager_id])
                    loss_reason = None
                    if slug == "lost":
                        loss_reason = RNG.choice(LOSS_REASONS)
                    conn.execute(text("""
                        INSERT INTO lead_stage_log
                            (contact_id, stage_id, changed_by, changed_at, loss_reason)
                        VALUES (:cid, :sid, :by, :at, :lr)
                    """), {"cid": cid, "sid": sid, "by": actor,
                           "at": changed_at, "lr": loss_reason})
                    stage_log_count += 1

        conn.commit()
        print(f"    {stage_log_count} stage log rows inserted.")

        # ── 12. Interactions ──────────────────────────────────────────────────
        print("  Generating interactions …")
        all_contact_ids = contact_ids["consumer"] + contact_ids["partner"] + contact_ids["dealer"]
        all_lead_ids    = [lid for lids in lead_ids_by_status.values() for lid in lids]

        activity_types  = ["call", "field_visit", "note", "message", "whatsapp", "phone_call"]
        act_weights     = [35, 15, 20, 15, 10, 5]
        outcomes        = ["done", "not_now", "no_answer"]
        outcome_weights = [40, 30, 30]
        priorities      = ["high", "medium", "low", "none"]
        prio_weights    = [15, 35, 30, 20]
        task_titles     = [
            "Follow up on quote", "Site visit scheduled", "Demo appointment",
            "Call back after 3pm", "Send product brochure", "Confirm delivery date",
            "Collect payment", "Installation follow-up", "Referral thank-you call",
            "Re-engage lost lead", "Post-sale check-in", "Welcome call",
        ]

        interaction_count = 0

        # 800 activity logs: split across leads and contacts
        for i in range(800):
            use_lead = RNG.random() < 0.45
            if use_lead:
                eid   = RNG.choice(all_lead_ids)
                etype = "lead"
            else:
                eid   = RNG.choice(all_contact_ids)
                etype = "contact"

            actor      = RNG.choice(all_staff)
            atype      = _weighted(activity_types, act_weights)
            outcome    = _weighted(outcomes, outcome_weights) if atype in ("call", "phone_call") else None
            logged_at  = _rand_date(START_DATE, END_DATE)

            conn.execute(text("""
                INSERT INTO interactions
                    (entity_type, entity_id, kind, activity_type, notes,
                     done_by, created_by, outcome, logged_at, created_at)
                VALUES
                    (:etype, :eid, 'log', :atype, :notes,
                     :actor, :actor, :outcome, :logged_at, :logged_at)
            """), {
                "etype": etype, "eid": eid, "atype": atype,
                "notes": RNG.choice(NOTES_POOL),
                "actor": actor, "outcome": outcome, "logged_at": logged_at,
            })
            interaction_count += 1

        # 400 tasks: 200 completed, 100 open/upcoming, 100 overdue
        today = datetime.datetime(2026, 11, 28, 12, 0, 0)
        task_specs = [
            # (count, status, due_offset_days_from_today, completed)
            (200, "completed", -RNG.randint(1, 60), True),
            (100, "to_do",      RNG.randint(1, 14),  False),
            (100, "to_do",     -RNG.randint(1, 30),  False),   # overdue (due in past, not done)
        ]
        for count, status, _, is_done in task_specs:
            for _ in range(count):
                use_lead = RNG.random() < 0.4
                eid   = RNG.choice(all_lead_ids) if use_lead else RNG.choice(all_contact_ids)
                etype = "lead" if use_lead else "contact"
                assignee = RNG.choice(all_staff)
                priority = _weighted(priorities, prio_weights)

                if is_done:
                    due_at = today - datetime.timedelta(days=RNG.randint(1, 60))
                elif status == "to_do" and _ < 100:
                    due_at = today + datetime.timedelta(days=RNG.randint(1, 14))
                else:
                    due_at = today - datetime.timedelta(days=RNG.randint(1, 30))

                created_at = due_at - datetime.timedelta(days=RNG.randint(1, 7))

                # Insert the task
                conn.execute(text("""
                    INSERT INTO interactions
                        (entity_type, entity_id, kind, title, notes,
                         assigned_to, created_by, due_at, priority, status, created_at)
                    VALUES
                        (:etype, :eid, 'task', :title, :notes,
                         :assignee, :creator, :due_at, :priority, :status, :created_at)
                """), {
                    "etype": etype, "eid": eid,
                    "title": RNG.choice(task_titles),
                    "notes": RNG.choice(NOTES_POOL),
                    "assignee": assignee,
                    "creator": RNG.choice(sales_ids + [manager_id]),
                    "due_at": due_at,
                    "priority": priority,
                    "status": status,
                    "created_at": created_at,
                })
                task_id = _one(conn, "SELECT lastval()")[0]
                interaction_count += 1

                # For completed tasks, insert a completion log row
                if is_done:
                    done_at = due_at + datetime.timedelta(hours=RNG.randint(1, 8))
                    conn.execute(text("""
                        INSERT INTO interactions
                            (entity_type, entity_id, kind, activity_type, notes,
                             done_by, created_by, outcome, logged_at, created_at,
                             parent_task_id)
                        VALUES
                            (:etype, :eid, 'log', 'call', :notes,
                             :actor, :actor, 'done', :done_at, :done_at,
                             :task_id)
                    """), {
                        "etype": etype, "eid": eid,
                        "notes": "Task completed.",
                        "actor": assignee, "done_at": done_at,
                        "task_id": task_id,
                    })
                    # Mark task completed
                    conn.execute(text(
                        "UPDATE interactions SET status = 'completed' WHERE id = :id"
                    ), {"id": task_id})
                    interaction_count += 1

        conn.commit()
        print(f"    {interaction_count} interaction rows inserted.")

        # ── 13. Record logs (500) ─────────────────────────────────────────────
        print("  Generating record logs …")
        rl_count = 0

        # Conversion logs (one per converted lead)
        for lead_id in converted_lead_ids:
            lrow = _one(conn, "SELECT assigned_to, created_at FROM leads WHERE id = :id", {"id": lead_id})
            actor = lrow[0] or RNG.choice(sales_ids)
            converted_at = lrow[1] + datetime.timedelta(days=RNG.randint(1, 20))
            conn.execute(text("""
                INSERT INTO record_logs (entity_type, entity_id, action, actor_id, created_at)
                VALUES ('lead', :eid, 'converted', :actor, :at)
            """), {"eid": lead_id, "actor": actor, "at": converted_at})
            rl_count += 1

        # Assignment logs
        sample_leads = RNG.sample(all_lead_ids, min(150, len(all_lead_ids)))
        for lead_id in sample_leads:
            actor  = RNG.choice(sales_ids + [manager_id])
            new_assignee = RNG.choice(sales_ids)
            at = _rand_date(START_DATE, END_DATE)
            conn.execute(text("""
                INSERT INTO record_logs
                    (entity_type, entity_id, action, actor_id, new_value, created_at)
                VALUES ('lead', :eid, 'assigned', :actor, :nv, :at)
            """), {"eid": lead_id, "actor": actor, "nv": str(new_assignee), "at": at})
            rl_count += 1

        # Stage-change logs on contacts
        sample_contacts = RNG.sample(all_contact_ids, min(100, len(all_contact_ids)))
        stage_names = [s["name"] for s in PIPELINE_STAGES["consumer"]]
        for cid in sample_contacts:
            actor = RNG.choice(sales_ids)
            at    = _rand_date(START_DATE, END_DATE)
            old_s = RNG.choice(stage_names[:-2])
            new_s = RNG.choice(stage_names[1:])
            conn.execute(text("""
                INSERT INTO record_logs
                    (entity_type, entity_id, action, actor_id, old_value, new_value, created_at)
                VALUES ('contact', :eid, 'stage_changed', :actor, :old, :new, :at)
            """), {"eid": cid, "actor": actor, "old": old_s, "new": new_s, "at": at})
            rl_count += 1

        conn.commit()
        print(f"    {rl_count} record log rows inserted.")

        # ── Summary ───────────────────────────────────────────────────────────
        print("\n" + "─" * 55)
        print(f"  ✓ Schema         : {schema}")
        print(f"  ✓ Employees      : 6")
        print(f"  ✓ Leads          : {len(all_leads)}")
        print(f"  ✓ Contacts       : {total_contacts} "
              f"(consumer={len(contact_ids['consumer'])}, "
              f"partner={len(contact_ids['partner'])}, "
              f"dealer={len(contact_ids['dealer'])})")
        print(f"  ✓ Stage log rows : {stage_log_count}")
        print(f"  ✓ Interactions   : {interaction_count}")
        print(f"  ✓ Record logs    : {rl_count}")
        print("─" * 55)
        print("\nLogin credentials (all accounts):")
        print(f"  Admin   : {ADMIN_EMAIL}")
        print(f"  Manager : {MANAGER_EMAIL}")
        print(f"  Sales   : {SALES1_EMAIL}, {SALES2_EMAIL}, {SALES3_EMAIL}")
        print(f"  Tele    : {TELE_EMAIL}")
        print(f"  Password: {DEMO_PASSWORD}\n")


def _print_plan():
    print("  Planned inserts:")
    total_stage_logs = sum(
        sum(STAGE_MINIMUMS[ct].values()) for ct in STAGE_MINIMUMS
    )
    rows = [
        ("Employees",       6),
        ("Leads",        1000),
        ("Contacts",      350, "(consumer=250, partner=70, dealer=30)"),
        ("Stage log rows", total_stage_logs, "(minimum guaranteed)"),
        ("Interactions",  1200, "(800 logs + 400 tasks)"),
        ("Record logs",    500, ""),
    ]
    for r in rows:
        label, count = r[0], r[1]
        note = r[2] if len(r) > 2 else ""
        print(f"    {label:<20} {count:>5}  {note}")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Seed a demo tenant.")
    p.add_argument("--schema",  default=DEFAULT_SCHEMA, help="PostgreSQL schema name")
    p.add_argument("--dry-run", action="store_true",    help="Print plan, write nothing")
    p.add_argument("--reset",   action="store_true",    help="Drop schema and re-seed")
    args = p.parse_args()

    if args.schema == "ventino" or args.schema == "public":
        print(f"ERROR: Refusing to seed '{args.schema}' — that is a production schema.")
        sys.exit(1)

    seed(schema=args.schema, dry_run=args.dry_run, reset=args.reset)
