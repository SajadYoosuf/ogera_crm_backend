"""
import_excel_as_leads.py

Reads the same Excel files as excel_to_wacrm_csv.py but inserts the records
directly into the PostgreSQL ventino.leads table instead of producing a CSV.

HOW TO RUN:
    cd ogera_crm_backend
    python scripts/import_excel_as_leads.py

    # Dry run (shows what would be inserted without touching the DB):
    python scripts/import_excel_as_leads.py --dry-run

    # Target a different tenant schema:
    python scripts/import_excel_as_leads.py --schema ventino

WHAT IT DOES:
    1. Reads all Excel files in SOURCES (same logic as excel_to_wacrm_csv.py).
    2. Normalises phone numbers to E.164 and computes phone_key (last 10 digits).
    3. For each record, checks if phone_key already exists in leads.
    4. If not a duplicate -> inserts a new lead row.
    5. Prints a summary: inserted / skipped (dup) / skipped (invalid phone).

INGESTION METHOD MAPPING:
    customers / buyers  -> "manual"
    form                -> "website"
    ad_lead (Onam, Monsoon) -> "meta_ads"
"""

import argparse
import os
import re
import sys

_SAFE_SCHEMA = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    # Load .env from the backend root so DB credentials are picked up
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass  # python-dotenv optional; fall back to env vars already set

try:
    import openpyxl
except ImportError:
    sys.exit("ERROR: openpyxl not installed. Run: pip install openpyxl")

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    sys.exit("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")


# ── DB config (from environment only — no hardcoded credentials) ──────────────

def get_db_config():
    missing = [k for k in (
        "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
        "POSTGRES_USER", "POSTGRES_PASSWORD",
    ) if not os.getenv(k)]
    if missing:
        sys.exit(
            f"ERROR: Missing environment variables: {', '.join(missing)}\n"
            f"Make sure .env is present in the backend root or these vars are set."
        )
    return {
        "host":     os.environ["POSTGRES_HOST"],
        "port":     int(os.environ["POSTGRES_PORT"]),
        "dbname":   os.environ["POSTGRES_DB"],
        "user":     os.environ["POSTGRES_USER"],
        "password": os.environ["POSTGRES_PASSWORD"],
    }


# ── Source Excel files ────────────────────────────────────────────────────────

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

"""
source_id mapping (from lead_sources table seed data):
  1 = Walk-in   → CUSTOMERS (manual walk-in)
  3 = Meta Ads  → Onam / Monsoon ad leads
  4 = Website   → FORM (website form)
"""

SOURCES = [
    {
        "file":      os.path.join(BACKEND_DIR, "CUSTOMERS.xlsx"),
        "label":     "CUSTOMERS",
        "tags":      ["General Enquiry"],
        "source_id": 1,   # Walk-in
        "cols":      {"name": "NAME ", "phone": "NUMBER", "location": "LOCATION"},
        "location_col": "location",
    },
    {
        "file":      os.path.join(BACKEND_DIR, "FORM AS  2.xlsx"),
        "label":     "FORM",
        "tags":      ["Form Lead"],
        "source_id": 4,   # Website
        "cols":      {"name": "name", "phone": "number"},
        "location_col": None,
    },
    {
        "file":      os.path.join(BACKEND_DIR, "Onam Offers.xlsx"),
        "label":     "ONAM",
        "tags":      ["Onam Ad Lead"],
        "source_id": 3,   # Meta Ads
        "cols":      {
            "name":    "full_name",
            "phone":   "phone_number",
            "address": "street_address",
            "city":    "city",
        },
        "location_col": "city",
    },
    {
        "file":      os.path.join(BACKEND_DIR, "Monsoon Offer.xlsx"),
        "label":     "MONSOON",
        "tags":      ["Monsoon Ad Lead"],
        "source_id": 3,   # Meta Ads
        "cols":      {
            "name":    "full_name",
            "phone":   "phone_number",
            "address": "street_address",
            "city":    "city",
        },
        "location_col": "city",
    },
    {
        "file":      os.path.join(BACKEND_DIR, "Monsoon Offer Aug 2026.xlsx"),
        "label":     "MONSOON_AUG26",
        "tags":      ["Monsoon Aug26 Ad Lead"],
        "source_id": 3,   # Meta Ads
        "cols":      {
            "name":    "full_name",
            "phone":   "phone_number",
            "address": "street_address",
            "city":    "city",
        },
        "location_col": "city",
    },
]


# ── Phone normalisation ───────────────────────────────────────────────────────

def normalize_phone(raw) -> "str | None":
    """
    Convert a raw phone value to E.164 format.
    Returns None for obviously fake / invalid numbers.

    WHY: We need a consistent format so phone_key (last 10 digits) is
    the same regardless of how the number was entered in the Excel file.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if s.lower().startswith("p:"):
        s = s[2:].strip()
    if "<" in s or "test" in s.lower() or "dummy" in s.lower():
        return None
    s = s.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if s.startswith("+91") and re.fullmatch(r"\+91[6-9]\d{9}", s):
        return s
    if s.startswith("+971") and re.fullmatch(r"\+971\d{9}", s):
        return s
    if s.startswith("+") and len(s) > 10:
        digits = s[1:]
        if re.fullmatch(r"\d{10,15}", digits):
            return s
        return None
    gcc_match = re.fullmatch(r"(971\d{9}|965\d{8}|973\d{8}|974\d{8}|966\d{9})", s)
    if gcc_match:
        return f"+{s}"
    if s.startswith("0") and len(s) == 11:
        s = s[1:]
    if s.startswith("91") and len(s) == 12:
        s = s[2:]
    if re.fullmatch(r"[6-9]\d{9}", s):
        return f"+91{s}"
    return None


def compute_phone_key(e164: str) -> str:
    """Last 10 digits — same dedup key used in contacts and leads tables."""
    digits = re.sub(r"\D", "", e164)
    return digits[-10:] if len(digits) >= 10 else digits


# ── Score (mirrors lead_service.compute_score) ────────────────────────────────

SOURCE_SCORE = {1: 10, 2: 20, 3: 30, 4: 25, 5: 20, 6: 15, 7: 20, 8: 15}


def compute_score(source_id: int) -> int:
    # +10 for phone (always present in these Excel files), capped at 100
    return min(SOURCE_SCORE.get(source_id, 10) + 10, 100)


# ── Location helpers ──────────────────────────────────────────────────────────

LOCATION_MAP = {
    "uragam": "Uragam", "valiyangadi": "Valiyangadi", "kozhikode": "Kozhikode",
    "kannur": "Kannur", "panakkad": "Panakkad", "malappuram": "Malappuram",
    "thrissur": "Thrissur", "perinthalmanna": "Perinthalmanna", "coimbatore": "Coimbatore",
    "vengara": "Vengara", "kanhangad": "Kanhangad", "kochi": "Kochi",
    "calicut": "Kozhikode", "muvattupuzha": "Muvattupuzha", "kasargode": "Kasargod",
    "thodupuzha": "Thodupuzha", "kottayam": "Kottayam", "pattambi": "Pattambi",
    "nilambur": "Nilambur", "tirur": "Tirur", "tirurangadi": "Tirurangadi",
}


def normalise_location(raw) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    return LOCATION_MAP.get(s.lower(), s.title())


def city_from_address(address) -> str:
    if not address:
        return ""
    parts = [p.strip() for p in str(address).split(",") if p.strip()]
    for part in reversed(parts):
        if re.fullmatch(r"\d{6}", part):
            continue
        return part.title()
    return ""


# ── Excel loader ──────────────────────────────────────────────────────────────

def load_source(source: dict) -> "tuple[list[dict], int]":
    """
    Read one Excel file and return a list of normalised dicts plus a count
    of rows skipped because the phone was invalid or missing.
    """
    path = source["file"]
    if not os.path.exists(path):
        print(f"  [SKIP] File not found: {path}")
        return [], 0

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    raw_rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if not raw_rows:
        return [], 0

    col_map = source["cols"]
    header = {str(c).strip().lower() if c else "": i for i, c in enumerate(raw_rows[0])}

    def get(row, col_key):
        col_name = col_map.get(col_key, "").strip().lower()
        idx = header.get(col_name)
        if idx is None or idx >= len(row):
            return None
        v = row[idx]
        return str(v).strip() if v is not None else None

    rows = []
    skipped = 0
    loc_col = source.get("location_col")

    for row in raw_rows[1:]:
        raw_phone = get(row, "phone")
        name = (get(row, "name") or "").title().strip()

        location = ""
        if loc_col == "location":
            location = normalise_location(get(row, "location"))
        elif loc_col == "address":
            location = city_from_address(get(row, "address"))
        elif loc_col == "city":
            location = normalise_location(get(row, "city")) or city_from_address(get(row, "address"))

        phone = normalize_phone(raw_phone)
        if not phone:
            skipped += 1
            continue

        # Split "Ravi Kumar Nair" → first_name="Ravi", last_name="Kumar Nair"
        parts = name.split(None, 1) if name else []
        first_name = parts[0] if parts else None
        last_name  = parts[1] if len(parts) > 1 else None

        source_id = source["source_id"]
        rows.append({
            "first_name": first_name,
            "last_name":  last_name,
            "phone":      phone,
            "phone_key":  compute_phone_key(phone),
            "address":    location or None,
            "tags":       list(source["tags"]),
            "source_id":  source_id,
            "score":      compute_score(source_id),
        })

    return rows, skipped


# ── Merge duplicates across sources ──────────────────────────────────────────

def merge_sources() -> "tuple[list[dict], int]":
    """
    Merge all Excel sources into one list, deduplicating by phone_key.
    When the same phone appears in multiple files, keep the best name /
    location and pick the ingestion_method with the highest score.
    """
    merged: "dict[str, dict]" = {}
    total_skipped = 0

    for source in SOURCES:
        label = source["label"]
        print(f"  Loading {label} ...", end=" ", flush=True)
        rows, skipped = load_source(source)
        total_skipped += skipped
        print(f"{len(rows)} valid, {skipped} skipped (bad phone)")

        for row in rows:
            pk = row["phone_key"]
            if pk in merged:
                existing = merged[pk]
                if not existing["first_name"] and row["first_name"]:
                    existing["first_name"] = row["first_name"]
                if not existing["address"] and row["address"]:
                    existing["address"] = row["address"]
                existing["tags"] = list(set(existing["tags"]) | set(row["tags"]))
                if row["score"] > existing["score"]:
                    existing["source_id"] = row["source_id"]
                    existing["score"] = row["score"]
            else:
                merged[pk] = row

    return list(merged.values()), total_skipped


# ── Insert into PostgreSQL ────────────────────────────────────────────────────

INSERT_SQL = """
INSERT INTO leads (
    first_name, last_name, phone, phone_key, address, tags,
    source_id, score, status, whatsapp_sent,
    created_at, updated_at
)
VALUES (
    %(first_name)s, %(last_name)s, %(phone)s, %(phone_key)s, %(address)s, %(tags)s,
    %(source_id)s, %(score)s, 'new', false,
    %(now)s, %(now)s
)
ON CONFLICT DO NOTHING
"""


def run_import(schema: str, rows: "list[dict]", dry_run: bool) -> dict:
    """
    Connect to the DB, check which phone_keys already exist, then insert
    the new ones.  dry_run=True rolls back the transaction without writing.
    """
    db_config = get_db_config()
    conn = psycopg2.connect(**db_config)
    conn.autocommit = False
    cur = conn.cursor()

    # Switch to the tenant schema — validate first to prevent SQL injection
    if not _SAFE_SCHEMA.match(schema):
        sys.exit(f"ERROR: Unsafe schema name '{schema}'. Only letters, digits, and underscores are allowed.")
    cur.execute(f"SET search_path TO {schema}")

    # Pre-fetch existing phone_keys to avoid per-row queries
    all_keys = [r["phone_key"] for r in rows]
    cur.execute("SELECT phone_key FROM leads WHERE phone_key = ANY(%s)", (all_keys,))
    existing_keys = {r[0] for r in cur.fetchall()}

    now = datetime.now(timezone.utc)
    inserted = 0
    skipped_dup = 0

    for row in rows:
        if row["phone_key"] in existing_keys:
            skipped_dup += 1
            continue

        if not dry_run:
            cur.execute(INSERT_SQL, {
                "first_name": row["first_name"] or "Unknown",
                "last_name":  row["last_name"],
                "phone":      row["phone"],
                "phone_key":  row["phone_key"],
                "address":    row["address"],
                "tags":       row["tags"],
                "source_id":  row["source_id"],
                "score":      row["score"],
                "now":        now,
            })

        inserted += 1
        existing_keys.add(row["phone_key"])  # prevent double-insert within batch

    if not dry_run:
        conn.commit()
    else:
        conn.rollback()

    cur.close()
    conn.close()
    return {"inserted": inserted, "skipped_dup": skipped_dup, "total": len(rows)}


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Import Excel contacts as leads into PostgreSQL."
    )
    parser.add_argument("--schema",  default="ventino", help="Tenant schema name (default: ventino)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be inserted without writing")
    args = parser.parse_args()

    print(f"\n=== Excel -> PostgreSQL Leads Import {'(DRY RUN) ' if args.dry_run else ''}===")
    print(f"Schema : {args.schema}\n")
    print("Reading Excel files:")

    rows, skipped_phone = merge_sources()

    tag_counts: "dict[str, int]" = {}
    for r in rows:
        for t in r["tags"]:
            tag_counts[t] = tag_counts.get(t, 0) + 1

    print(f"\nAfter merge:")
    print(f"  Unique records (valid phone) : {len(rows)}")
    print(f"  Skipped (invalid phone)      : {skipped_phone}")
    print(f"\nTag breakdown:")
    for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1]):
        print(f"  {tag:<35} {count}")

    if not rows:
        print("\nNothing to insert.")
        return

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Inserting into {args.schema}.leads ...")
    stats = run_import(args.schema, rows, args.dry_run)

    print(f"\n=== Done ===")
    print(f"  Inserted                : {stats['inserted']}")
    print(f"  Skipped (already in DB) : {stats['skipped_dup']}")
    print(f"  Total from Excel        : {stats['total']}")
    if args.dry_run:
        print("\n  --dry-run set. No rows written.")


if __name__ == "__main__":
    main()
