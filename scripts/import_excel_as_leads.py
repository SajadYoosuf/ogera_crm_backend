"""
import_excel_as_leads.py
========================
Imports leads from Excel files into PostgreSQL AND fixes created_at dates.

Five files are supported, each with a different structure:

  File                       | Has date? | Date column   | Format
  ---------------------------|-----------|---------------|---------------------------
  CUSTOMERS.xlsx             | NO        | —             | keep whatever DB has
  FORM AS  2.xlsx            | YES       | DATE          | D.M.YYYY  (e.g. 1.7.2026)
  Onam Offers.xlsx           | YES       | created_time  | ISO 8601 with tz offset
  Monsoon Offer.xlsx         | YES       | created_time  | ISO 8601 with tz offset
  Monsoon Offer Aug 2026.xlsx| YES       | created_time  | ISO 8601 with tz offset

For NEW leads  : uses the Excel date (if present) as created_at instead of now().
For EXISTING leads : if the Excel date is OLDER than the current DB created_at,
                     the row is updated.  CUSTOMERS has no date so its rows are
                     never touched even if they already exist.

HOW TO RUN:
    cd ogera_crm_backend

    # Dry run — shows plan without writing:
    python scripts/import_excel_as_leads.py --dry-run

    # Live run:
    python scripts/import_excel_as_leads.py

    # Different tenant schema (default: ventino):
    python scripts/import_excel_as_leads.py --schema ogera
"""

import argparse
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

_SAFE_SCHEMA = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

try:
    import openpyxl
except ImportError:
    sys.exit("ERROR: openpyxl not installed. Run: pip install openpyxl")

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    sys.exit("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")


# ── DB config ─────────────────────────────────────────────────────────────────

def get_db_config():
    missing = [k for k in (
        "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
        "POSTGRES_USER", "POSTGRES_PASSWORD",
    ) if not os.getenv(k)]
    if missing:
        sys.exit(f"ERROR: Missing environment variables: {', '.join(missing)}")
    return {
        "host":     os.environ["POSTGRES_HOST"],
        "port":     int(os.environ["POSTGRES_PORT"]),
        "dbname":   os.environ["POSTGRES_DB"],
        "user":     os.environ["POSTGRES_USER"],
        "password": os.environ["POSTGRES_PASSWORD"],
    }


# ── Source file definitions ───────────────────────────────────────────────────

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

"""
source_id mapping (from lead_sources seed):
  1 = Walk-in   → CUSTOMERS
  3 = Meta Ads  → Onam / Monsoon ad leads
  4 = Website   → FORM
"""

SOURCES = [
    {
        "file":      os.path.join(BACKEND_DIR, "CUSTOMERS.xlsx"),
        "label":     "CUSTOMERS",
        "tags":      ["General Enquiry"],
        "source_id": 1,
        "cols":      {"name": "NAME ", "phone": "NUMBER", "location": "LOCATION"},
        "location_col": "location",
        # NO date column — created_at comes from whatever the DB already has;
        # for new inserts we fall back to now().
        "date_col":    None,
        "date_format": None,
    },
    {
        "file":      os.path.join(BACKEND_DIR, "FORM AS  2.xlsx"),
        "label":     "FORM",
        "tags":      ["Form Lead"],
        "source_id": 4,
        "cols":      {"name": "NAME ", "phone": "NUMBER", "address": "ADDRESS"},
        "location_col": "address",
        # DATE column contains strings like "1.7.2026" or "25.7.2026"
        "date_col":    "DATE ",
        "date_format": "form",   # D.M.YYYY
    },
    {
        "file":      os.path.join(BACKEND_DIR, "Onam Offers.xlsx"),
        "label":     "ONAM",
        "tags":      ["Onam Ad Lead"],
        "source_id": 3,
        "cols":      {
            "name":    "full_name",
            "phone":   "phone_number",
            "address": "street_address",
            "city":    "city",
        },
        "location_col": "city",
        # created_time is ISO 8601 with timezone: "2026-08-19T12:50:51-05:00"
        "date_col":    "created_time",
        "date_format": "meta_iso",
    },
    {
        "file":      os.path.join(BACKEND_DIR, "Monsoon Offer.xlsx"),
        "label":     "MONSOON",
        "tags":      ["Monsoon Ad Lead"],
        "source_id": 3,
        "cols":      {
            "name":    "full_name",
            "phone":   "phone_number",
            "address": "street_address",
            "city":    "city",
        },
        "location_col": "city",
        "date_col":    "created_time",
        "date_format": "meta_iso",
    },
    {
        "file":      os.path.join(BACKEND_DIR, "Monsoon Offer Aug 2026.xlsx"),
        "label":     "MONSOON_AUG26",
        "tags":      ["Monsoon Aug26 Ad Lead"],
        "source_id": 3,
        "cols":      {
            "name":    "full_name",
            "phone":   "phone_number",
            "address": "street_address",
            "city":    "city",
        },
        "location_col": "city",
        "date_col":    "created_time",
        "date_format": "meta_iso",
    },
]


# ── Date parsers ──────────────────────────────────────────────────────────────

def parse_form_date(raw: str) -> "datetime | None":
    """
    Parse dates like "1.7.2026" or "25.7.2026" (D.M.YYYY).
    Returns a timezone-aware UTC datetime at midnight on that day.
    """
    if not raw or str(raw).strip() in ("", "None"):
        return None
    s = str(raw).strip()
    # Try D.M.YYYY first, then other common separators
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt  # naive UTC midnight
        except ValueError:
            continue
    return None


def parse_meta_iso(raw: str) -> "datetime | None":
    """
    Parse ISO 8601 timestamps that Meta Ads exports, e.g.:
      "2026-08-19T12:50:51-05:00"
    Returns a naive UTC datetime (tzinfo stripped) for consistent DB storage.
    """
    if not raw or str(raw).strip() in ("", "None"):
        return None
    s = str(raw).strip()
    try:
        # Python 3.7+ fromisoformat handles offset notation
        dt = datetime.fromisoformat(s)
        # Convert to UTC, then strip tzinfo so it matches the DB column type
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def parse_date(raw, fmt: "str | None") -> "datetime | None":
    """Route to the right parser based on the source's date_format setting."""
    if fmt is None or raw is None:
        return None
    if fmt == "form":
        return parse_form_date(raw)
    if fmt == "meta_iso":
        return parse_meta_iso(raw)
    return None


# ── Phone normalisation ───────────────────────────────────────────────────────

def normalize_phone(raw) -> "str | None":
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
    digits = re.sub(r"\D", "", e164)
    return digits[-10:] if len(digits) >= 10 else digits


# ── Lead scoring ──────────────────────────────────────────────────────────────

SOURCE_SCORE = {1: 10, 2: 20, 3: 30, 4: 25, 5: 20, 6: 15, 7: 20, 8: 15}

def compute_score(source_id: int) -> int:
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
    "ekm": "Ernakulam",
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
    Read one Excel file and return normalised lead dicts + count of bad-phone rows.

    Each dict has:
      first_name, last_name, phone, phone_key, address, tags,
      source_id, score, lead_date (datetime | None)

    lead_date is None for CUSTOMERS (no date column) and for rows where the
    date cell is empty or unparseable.
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

    col_map  = source["cols"]
    date_col = source.get("date_col")
    date_fmt = source.get("date_format")

    # Build header → column index map (case-insensitive, strip whitespace)
    header = {str(c).strip().lower() if c else "": i for i, c in enumerate(raw_rows[0])}

    def get(row, col_key):
        """Fetch a cell value by logical column key (via col_map)."""
        col_name = col_map.get(col_key, "").strip().lower()
        idx = header.get(col_name)
        if idx is None or idx >= len(row):
            return None
        v = row[idx]
        return str(v).strip() if v is not None else None

    def get_raw(row, col_name: str):
        """Fetch a cell value by exact column header name."""
        idx = header.get(col_name.strip().lower())
        if idx is None or idx >= len(row):
            return None
        return row[idx]

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

        parts = name.split(None, 1) if name else []
        first_name = parts[0] if parts else None
        last_name  = parts[1] if len(parts) > 1 else None

        # Parse date if this source has one
        lead_date = None
        if date_col:
            raw_date = get_raw(row, date_col)
            lead_date = parse_date(raw_date, date_fmt)

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
            "lead_date":  lead_date,   # None means "no date available from Excel"
        })

    return rows, skipped


# ── Merge duplicates across sources ──────────────────────────────────────────

def merge_sources() -> "tuple[list[dict], int]":
    """
    Load all Excel sources and deduplicate by phone_key.
    When the same phone appears in multiple files:
      - Keep the best name / address.
      - Merge tags.
      - Keep the OLDEST available date (most accurate original date).
      - Keep the highest-score source.
    """
    merged: "dict[str, dict]" = {}
    total_skipped = 0

    for source in SOURCES:
        label = source["label"]
        print(f"  Loading {label} ...", end=" ", flush=True)
        rows, skipped = load_source(source)
        total_skipped += skipped
        date_note = "(no date)" if source["date_col"] is None else f"(date: {source['date_col']})"
        print(f"{len(rows)} valid, {skipped} skipped (bad phone)  {date_note}")

        for row in rows:
            pk = row["phone_key"]
            if pk in merged:
                existing = merged[pk]
                if not existing["first_name"] and row["first_name"]:
                    existing["first_name"] = row["first_name"]
                if not existing["address"] and row["address"]:
                    existing["address"] = row["address"]
                existing["tags"] = list(set(existing["tags"]) | set(row["tags"]))
                # Keep the oldest date across sources
                if row["lead_date"] is not None:
                    if existing["lead_date"] is None or row["lead_date"] < existing["lead_date"]:
                        existing["lead_date"] = row["lead_date"]
                if row["score"] > existing["score"]:
                    existing["source_id"] = row["source_id"]
                    existing["score"] = row["score"]
            else:
                merged[pk] = row

    return list(merged.values()), total_skipped


# ── Database operations ───────────────────────────────────────────────────────

INSERT_SQL = """
INSERT INTO leads (
    first_name, last_name, phone, phone_key, address, tags,
    source_id, score, status, whatsapp_sent,
    created_at, updated_at
)
VALUES (
    %(first_name)s, %(last_name)s, %(phone)s, %(phone_key)s,
    %(address)s, %(tags)s, %(source_id)s, %(score)s,
    'new', false,
    %(created_at)s, %(now)s
)
ON CONFLICT DO NOTHING
"""

UPDATE_DATE_SQL = """
UPDATE leads
   SET created_at = %(new_date)s
 WHERE phone_key  = %(phone_key)s
   AND created_at > %(new_date)s
   AND deleted_at IS NULL
"""


def run_import(schema: str, rows: "list[dict]", dry_run: bool) -> dict:
    """
    1. INSERT rows whose phone_key isn't in the DB yet.
       - Uses lead_date as created_at when available, else now().
    2. UPDATE existing rows where the Excel date is older than the DB date.
       - Skips rows that have no date (CUSTOMERS).
    """
    db_config = get_db_config()
    conn = psycopg2.connect(**db_config)
    conn.autocommit = False
    cur = conn.cursor()

    if not _SAFE_SCHEMA.match(schema):
        sys.exit(f"ERROR: Unsafe schema name '{schema}'.")
    cur.execute(f"SET search_path TO {schema}")

    # Fetch existing leads so we can compare dates
    all_keys = [r["phone_key"] for r in rows]
    cur.execute(
        "SELECT phone_key, created_at FROM leads WHERE phone_key = ANY(%s) AND deleted_at IS NULL",
        (all_keys,)
    )
    existing: "dict[str, datetime]" = {}
    for pk, ca in cur.fetchall():
        # Normalise to naive UTC for comparison
        if ca is not None and hasattr(ca, "tzinfo") and ca.tzinfo is not None:
            ca = ca.replace(tzinfo=None)
        existing[pk] = ca

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC

    inserted     = 0
    dates_fixed  = 0
    skipped_dup  = 0  # already in DB, date already correct or no date available

    for row in rows:
        pk         = row["phone_key"]
        lead_date  = row["lead_date"]  # naive datetime or None

        if pk not in existing:
            # ── New lead — insert with correct date ───────────────────────────
            created_at = lead_date if lead_date is not None else now_utc
            if not dry_run:
                cur.execute(INSERT_SQL, {
                    "first_name": row["first_name"] or "Unknown",
                    "last_name":  row["last_name"],
                    "phone":      row["phone"],
                    "phone_key":  pk,
                    "address":    row["address"],
                    "tags":       row["tags"],
                    "source_id":  row["source_id"],
                    "score":      row["score"],
                    "created_at": created_at,
                    "now":        now_utc,
                })
            existing[pk] = created_at  # prevent double-insert in this batch
            inserted += 1

        else:
            # ── Existing lead — maybe fix its created_at ──────────────────────
            if lead_date is None:
                # No date in Excel (e.g. CUSTOMERS) — nothing we can fix
                skipped_dup += 1
                continue

            db_date = existing[pk]
            # db_date may be None if the column was NULL
            if db_date is None or lead_date < db_date:
                if not dry_run:
                    cur.execute(UPDATE_DATE_SQL, {
                        "phone_key": pk,
                        "new_date":  lead_date,
                    })
                dates_fixed += 1
            else:
                skipped_dup += 1  # DB date already correct

    if not dry_run:
        conn.commit()
    else:
        conn.rollback()

    cur.close()
    conn.close()
    return {
        "inserted":    inserted,
        "dates_fixed": dates_fixed,
        "skipped_dup": skipped_dup,
        "total":       len(rows),
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Import Excel leads into PostgreSQL and fix created_at dates.\n"
            "CUSTOMERS has no date — those rows are inserted with today's date "
            "and are never date-updated.\n"
            "FORM / Onam / Monsoon have real dates that are used on insert and "
            "to backfill existing rows where the DB date is too recent."
        )
    )
    parser.add_argument("--schema",  default="ventino", help="Tenant schema (default: ventino)")
    parser.add_argument("--dry-run", action="store_true", help="Show plan without writing to DB")
    args = parser.parse_args()

    print(f"\n=== Excel → PostgreSQL Leads Import {'(DRY RUN) ' if args.dry_run else ''}===")
    print(f"Schema: {args.schema}\n")
    print("Reading Excel files:")

    rows, skipped_phone = merge_sources()

    # Print date coverage
    with_date    = sum(1 for r in rows if r["lead_date"] is not None)
    without_date = len(rows) - with_date
    tag_counts: "dict[str, int]" = {}
    for r in rows:
        for t in r["tags"]:
            tag_counts[t] = tag_counts.get(t, 0) + 1

    print(f"\nAfter merge:")
    print(f"  Unique records (valid phone)  : {len(rows)}")
    print(f"    With date from Excel        : {with_date}")
    print(f"    Without date (CUSTOMERS)    : {without_date}")
    print(f"  Skipped (invalid/missing phone): {skipped_phone}")
    print(f"\nTag breakdown:")
    for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1]):
        print(f"  {tag:<35} {count}")

    if not rows:
        print("\nNothing to process.")
        return

    # Show a sample of dates found
    sample_dates = [(r["phone_key"], r["lead_date"]) for r in rows if r["lead_date"]][:5]
    if sample_dates:
        print(f"\nSample dates from Excel:")
        for pk, dt in sample_dates:
            print(f"  phone_key={pk}  date={dt.date() if dt else 'N/A'}")

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Processing into {args.schema}.leads ...")
    stats = run_import(args.schema, rows, args.dry_run)

    print(f"\n=== Done ===")
    print(f"  New leads inserted             : {stats['inserted']}")
    print(f"  Existing leads — date fixed    : {stats['dates_fixed']}  (Excel date was older)")
    print(f"  Existing leads — date ok/no src: {stats['skipped_dup']}")
    print(f"  Total from Excel               : {stats['total']}")
    if args.dry_run:
        print("\n  --dry-run: nothing written to DB.")


if __name__ == "__main__":
    main()
