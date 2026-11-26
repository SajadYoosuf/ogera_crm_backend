"""
Migration 001 — Contacts Module MVP
====================================
Run once against each tenant schema that needs it.

What this creates (all additive — no drops, no data changes):
  1. pipeline_stages          — stage catalog per contact type
  2. lead_stage_log           — stage history per contact
  3. contact_assignments      — owner-change history per contact
  4. dealer_profiles          — dealer contact profile (new contact type)
  5. contact_field_definitions.show_in_list  — column added to existing table

After the tables are created the script seeds the default pipeline stages
(Consumer, Partner, Dealer) for every tenant schema it runs against.
Seed is idempotent — it skips rows whose slug already exists.

Usage
-----
  # Dry run (default) — prints SQL, touches nothing:
  python migrations/001_contacts_module_mvp.py

  # Apply to ventino schema on live DB:
  python migrations/001_contacts_module_mvp.py --apply --schema ventino

  # Apply to all tenant schemas found in public.tenants:
  python migrations/001_contacts_module_mvp.py --apply --all-tenants
"""
import argparse
import os
import sys

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def _connect():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        user=os.getenv("POSTGRES_USER", "ogera_crm"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        dbname=os.getenv("POSTGRES_DB", "ogera_crm"),
    )


# ---------------------------------------------------------------------------
# SQL definitions
# ---------------------------------------------------------------------------

def _sql_pipeline_stages(schema: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {schema}.pipeline_stages (
    id          SERIAL       PRIMARY KEY,
    name        VARCHAR(60)  NOT NULL,
    slug        VARCHAR(40)  NOT NULL,
    contact_type VARCHAR(20) NOT NULL,
    description  VARCHAR(255),
    "order"     SMALLINT     NOT NULL DEFAULT 0,
    is_active   SMALLINT     NOT NULL DEFAULT 1,
    CONSTRAINT uq_pipeline_stage_slug_per_type UNIQUE (slug, contact_type)
);
"""


def _sql_lead_stage_log(schema: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {schema}.lead_stage_log (
    id           BIGSERIAL    PRIMARY KEY,
    contact_id   BIGINT       NOT NULL REFERENCES {schema}.contacts(id),
    stage_id     INT          NOT NULL REFERENCES {schema}.pipeline_stages(id),
    changed_by   BIGINT       NOT NULL REFERENCES {schema}.employees(id),
    changed_at   TIMESTAMP    NOT NULL DEFAULT now(),
    loss_reason  VARCHAR(40),
    loss_notes   TEXT,
    awareness_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_lead_stage_log_contact_id ON {schema}.lead_stage_log(contact_id);
CREATE INDEX IF NOT EXISTS idx_lead_stage_log_stage_id   ON {schema}.lead_stage_log(stage_id);
"""


def _sql_contact_assignments(schema: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {schema}.contact_assignments (
    id            BIGSERIAL  PRIMARY KEY,
    contact_id    BIGINT     NOT NULL REFERENCES {schema}.contacts(id),
    assigned_from BIGINT     REFERENCES {schema}.employees(id),
    assigned_to   BIGINT     REFERENCES {schema}.employees(id),
    changed_by    BIGINT     NOT NULL REFERENCES {schema}.employees(id),
    changed_at    TIMESTAMP  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_contact_assignments_contact_id ON {schema}.contact_assignments(contact_id);
"""


def _sql_dealer_profiles(schema: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {schema}.dealer_profiles (
    id                  BIGSERIAL    PRIMARY KEY,
    contact_id          BIGINT       NOT NULL UNIQUE REFERENCES {schema}.contacts(id),
    business_name       VARCHAR(120),
    gstin               VARCHAR(20),
    town_city           VARCHAR(80),
    pin_code            VARCHAR(10),
    dealer_type         VARCHAR(60),
    assigned_rep        BIGINT       REFERENCES {schema}.employees(id),
    credit_limit        NUMERIC(12,2) NOT NULL DEFAULT 0,
    outstanding_balance NUMERIC(12,2) NOT NULL DEFAULT 0,
    data                JSONB        NOT NULL DEFAULT '{{}}'
);
CREATE INDEX IF NOT EXISTS idx_dealer_profiles_contact_id ON {schema}.dealer_profiles(contact_id);
"""


def _sql_add_show_in_list(schema: str) -> str:
    return f"""
ALTER TABLE {schema}.contact_field_definitions
    ADD COLUMN IF NOT EXISTS show_in_list BOOLEAN NOT NULL DEFAULT FALSE;
"""


# ---------------------------------------------------------------------------
# Pipeline stage seed data (per the plan in docs/contacts-module-mvp-plan.md)
# ---------------------------------------------------------------------------

CONSUMER_STAGES = [
    ("New Lead",                "new_lead",              1),
    ("First Call",              "first_call",            2),
    ("Connected",               "connected",             3),
    ("Aware",                   "aware",                 4),
    ("Interested",              "interested",            5),
    ("Quotation",               "quotation",             6),
    ("Negotiation",             "negotiation",           7),
    ("Won",                     "won",                   8),
    ("Lost",                    "lost",                  9),
    ("After Sales / Referring", "after_sales_referring", 10),
]

PARTNER_STAGES = [
    ("New Lead",           "new_lead",          1),
    ("First Call",         "first_call",        2),
    ("Connected",          "connected",         3),
    ("Demo / Introduction","demo_introduction", 4),
    ("Aware",              "aware",             5),
    ("Interested",         "interested",        6),
    ("Active",             "active",            7),
    ("Referral",           "referral",          8),
    ("Inactive",           "inactive",          9),
]

DEALER_STAGES = [
    ("Prospect",          "prospect",          1),
    ("Application Received", "application_received", 2),
    ("Verification",      "verification",      3),
    ("Agreement Signed",  "agreement_signed",  4),
    ("Active Dealer",     "active_dealer",     5),
    ("Inactive",          "inactive_dealer",   6),
]


def _seed_pipeline_stages(cur, schema: str, dry_run: bool) -> None:
    all_stages = (
        [(n, s, o, "consumer") for n, s, o in CONSUMER_STAGES]
        + [(n, s, o, "partner")  for n, s, o in PARTNER_STAGES]
        + [(n, s, o, "dealer")   for n, s, o in DEALER_STAGES]
    )
    for name, slug, order, contact_type in all_stages:
        sql = f"""
            INSERT INTO {schema}.pipeline_stages (name, slug, contact_type, "order", is_active)
            VALUES (%s, %s, %s, %s, 1)
            ON CONFLICT (slug, contact_type) DO NOTHING;
        """
        if dry_run:
            print(f"  [DRY] SEED pipeline_stages: {contact_type}/{slug}")
        else:
            cur.execute(sql, (name, slug, contact_type, order))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

STEPS = [
    ("pipeline_stages",          _sql_pipeline_stages),
    ("lead_stage_log",           _sql_lead_stage_log),
    ("contact_assignments",      _sql_contact_assignments),
    ("dealer_profiles",          _sql_dealer_profiles),
    ("contact_field_definitions.show_in_list", _sql_add_show_in_list),
]


def run(schema: str, dry_run: bool) -> None:
    print(f"\n{'[DRY RUN] ' if dry_run else ''}Running migration 001 on schema: {schema}\n")

    if dry_run:
        for label, sql_fn in STEPS:
            print(f"--- {label} ---")
            print(sql_fn(schema).strip())
            print()
        print("--- seed pipeline_stages ---")
        _seed_pipeline_stages(None, schema, dry_run=True)
        return

    conn = _connect()
    conn.autocommit = False
    cur = conn.cursor()

    try:
        for label, sql_fn in STEPS:
            print(f"  Creating/altering: {label} ...", end=" ")
            cur.execute(sql_fn(schema))
            print("OK")

        print("  Seeding pipeline stages ...", end=" ")
        _seed_pipeline_stages(cur, schema, dry_run=False)
        print("OK")

        conn.commit()
        print(f"\n✓ Migration 001 applied to {schema}\n")

    except Exception as exc:
        conn.rollback()
        print(f"\n✗ FAILED — rolled back. Error: {exc}")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


def _all_tenant_schemas(conn) -> list[str]:
    cur = conn.cursor()
    cur.execute("SELECT schema_name FROM public.tenants WHERE is_active = 1 ORDER BY schema_name")
    rows = cur.fetchall()
    cur.close()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migration 001 — Contacts Module MVP")
    parser.add_argument("--apply",       action="store_true", help="Actually run against the DB (default: dry run)")
    parser.add_argument("--schema",      default="ventino",   help="Single tenant schema to migrate (default: ventino)")
    parser.add_argument("--all-tenants", action="store_true", help="Migrate every active tenant schema in public.tenants")
    args = parser.parse_args()

    dry_run = not args.apply

    if dry_run:
        print("=== DRY RUN — pass --apply to execute ===")

    if args.all_tenants:
        conn = _connect()
        schemas = _all_tenant_schemas(conn)
        conn.close()
        print(f"Tenant schemas found: {schemas}")
        for s in schemas:
            run(s, dry_run)
    else:
        run(args.schema, dry_run)
