"""
analyze_lead_dates.py
=====================
Compares the created_at dates in the leads table against the dates in a
CSV or Excel file so you can see which rows need to be fixed.

WHAT IT DOES:
  1. Reads a CSV or Excel file you give it.
  2. Fetches all leads from the tenant schema.
  3. Matches records by phone number (last 10 digits — same dedup logic the app uses).
  4. Shows a comparison table: DB date vs. file date for each matched lead.
  5. At the end prints a summary and, optionally, the UPDATE SQL to fix mismatches.

HOW TO RUN:
  cd ogera_crm_backend

  # just see the report (dry run):
  python scripts/analyze_lead_dates.py path/to/leads.csv
  python scripts/analyze_lead_dates.py path/to/leads.xlsx

  # different tenant schema (default: ventino):
  python scripts/analyze_lead_dates.py leads.csv --schema ogera

  # also print the SQL UPDATE statements that would fix mismatches:
  python scripts/analyze_lead_dates.py leads.csv --show-sql

  # actually apply the UPDATEs (writes to DB):
  python scripts/analyze_lead_dates.py leads.csv --apply

REQUIRED COLUMNS in the file (names are matched case-insensitively):
  - phone / mobile / phone_number / contact  — the lead's phone number
  - date / created_at / lead_date / created  — the correct date (any common format)

OPTIONAL COLUMNS (shown in the output but not required):
  - name / first_name / customer_name        — to identify the lead in the report
"""

import argparse
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# ── Load .env from the backend root ──────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass  # fall back to env vars already set


# ── Dependency checks ─────────────────────────────────────────────────────────
try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    sys.exit("ERROR: psycopg2 not installed.  Run: pip install psycopg2-binary")


# ── DB connection ─────────────────────────────────────────────────────────────

def get_db_config() -> dict:
    missing = [k for k in (
        "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
        "POSTGRES_USER", "POSTGRES_PASSWORD",
    ) if not os.getenv(k)]
    if missing:
        sys.exit(
            f"ERROR: Missing environment variables: {', '.join(missing)}\n"
            f"Make sure .env is in ogera_crm_backend/ or these vars are set."
        )
    return {
        "host":     os.environ["POSTGRES_HOST"],
        "port":     int(os.environ["POSTGRES_PORT"]),
        "dbname":   os.environ["POSTGRES_DB"],
        "user":     os.environ["POSTGRES_USER"],
        "password": os.environ["POSTGRES_PASSWORD"],
    }


# ── Phone normalisation ───────────────────────────────────────────────────────

def phone_key(raw: str) -> str | None:
    """Return the last 10 digits of a phone string, same as the app's phone_key."""
    digits = re.sub(r"\D", "", str(raw or ""))
    return digits[-10:] if len(digits) >= 10 else None


# ── Date parsing ──────────────────────────────────────────────────────────────

_DATE_FMTS = [
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y",
    "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
    "%Y/%m/%d",
]

def parse_date(val) -> date | None:
    """Try to turn a string, int (Excel serial), or datetime into a date."""
    if val is None or val == "":
        return None
    if isinstance(val, (datetime,)):
        return val.date()
    if isinstance(val, date):
        return val
    # Excel serial number (days since 1900-01-01)
    if isinstance(val, (int, float)):
        try:
            from datetime import timedelta
            return (datetime(1899, 12, 30) + timedelta(days=int(val))).date()
        except Exception:
            return None
    s = str(val).strip()
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ── Column detection ──────────────────────────────────────────────────────────

PHONE_ALIASES = {"phone", "mobile", "phone_number", "contact", "mob", "tel",
                 "phoneno", "phone no", "mobile no"}
DATE_ALIASES  = {"date", "created_at", "lead_date", "created", "enquiry_date",
                 "enquiry date", "leaddate", "entrydate", "entry_date", "ad_date"}
NAME_ALIASES  = {"name", "first_name", "customer_name", "fullname", "full_name",
                 "customer", "lead_name"}

def detect_col(headers: list[str], aliases: set[str]) -> str | None:
    for h in headers:
        if h.strip().lower() in aliases:
            return h
    return None


# ── File reading ──────────────────────────────────────────────────────────────

def read_file(path: Path) -> list[dict]:
    """Read a CSV, XLSX, or JSON file into a list of dicts."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        import csv
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    elif suffix in (".xlsx", ".xls"):
        try:
            import openpyxl
        except ImportError:
            sys.exit("ERROR: openpyxl not installed.  Run: pip install openpyxl")
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(h or "").strip() for h in rows[0]]
        return [dict(zip(headers, row)) for row in rows[1:]]
    elif suffix == ".json":
        import json
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            # Flatten any non-dict values just in case
            return [r for r in data if isinstance(r, dict)]
        # Handle {"data": [...]} or {"contacts": [...]} wrapper shapes
        for key in ("data", "contacts", "leads", "results", "items"):
            if key in data and isinstance(data[key], list):
                return [r for r in data[key] if isinstance(r, dict)]
        sys.exit(f"ERROR: JSON file does not contain a list of records (tried top-level and common wrapper keys).")
    else:
        sys.exit(f"ERROR: Unsupported file type '{suffix}'.  Use .csv, .xlsx, or .json")


# ── DB fetch ──────────────────────────────────────────────────────────────────

def fetch_leads(cfg: dict, schema: str) -> list[dict]:
    """Fetch all live leads from the tenant schema."""
    conn = psycopg2.connect(**cfg)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SET search_path TO {schema}")
            cur.execute("""
                SELECT id, first_name, last_name, phone, phone_key,
                       source_id, status, created_at
                FROM leads
                WHERE deleted_at IS NULL
                ORDER BY id
            """)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ── Apply UPDATEs ─────────────────────────────────────────────────────────────

def apply_updates(cfg: dict, schema: str, updates: list[tuple[int, date]]) -> int:
    """Apply created_at updates. Returns count of rows updated."""
    conn = psycopg2.connect(**cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {schema}")
            count = 0
            for lead_id, new_date in updates:
                ts = f"{new_date} 00:00:00+00"
                cur.execute(
                    "UPDATE leads SET created_at = %s WHERE id = %s",
                    (ts, lead_id),
                )
                count += cur.rowcount
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Compare lead created_at dates in the DB against a CSV/Excel file."
    )
    parser.add_argument("file", nargs="?", help="Path to a CSV or Excel file (omit to pick interactively)")
    parser.add_argument(
        "--schema", default="ventino",
        help="Tenant PostgreSQL schema (default: ventino)"
    )
    parser.add_argument(
        "--all", action="store_true", dest="scan_all",
        help="Scan all CSV/XLSX files in the current directory at once"
    )
    parser.add_argument(
        "--show-sql", action="store_true",
        help="Print the SQL UPDATE statements for mismatches"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually run the UPDATE statements (writes to DB)"
    )
    args = parser.parse_args()

    cfg = get_db_config()

    # ── Resolve which files to scan ───────────────────────────────────────────
    cwd = Path.cwd()
    candidates = sorted(
        list(cwd.glob("*.csv")) + list(cwd.glob("*.xlsx")) +
        list(cwd.glob("*.xls")) + list(cwd.glob("*.json"))
    )

    if args.scan_all:
        file_paths = candidates
        if not file_paths:
            sys.exit("ERROR: No .csv or .xlsx files found in the current directory.")
    elif args.file:
        p = Path(args.file)
        if not p.exists():
            sys.exit(f"ERROR: File not found: {p}")
        file_paths = [p]
    else:
        # Interactive picker
        if not candidates:
            sys.exit(
                "ERROR: No .csv, .xlsx, or .json files found in the current directory.\n"
                "  Pass the file path directly:\n"
                "    python scripts/analyze_lead_dates.py path/to/contacts.json\n"
                "  Or use --all to scan everything in the current folder."
            )
        print("\nNo file specified. Files found in current directory:")
        for i, p in enumerate(candidates, 1):
            print(f"  [{i}] {p.name}")
        print(f"  [all] scan all {len(candidates)} files at once")
        try:
            choice = input("\nEnter number, 'all', or paste a full path: ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit("\nCancelled.")

        if choice.lower() == "all":
            file_paths = candidates
        elif choice.isdigit() and 1 <= int(choice) <= len(candidates):
            file_paths = [candidates[int(choice) - 1]]
        elif "," in choice:
            # comma-separated numbers e.g. "1,3,5"
            selected = []
            for part in choice.split(","):
                part = part.strip()
                if part.isdigit() and 1 <= int(part) <= len(candidates):
                    selected.append(candidates[int(part) - 1])
                else:
                    print(f"  Skipping unrecognised selection: '{part}'")
            if not selected:
                sys.exit("No valid files selected.")
            file_paths = selected
        elif choice:
            p = Path(choice)
            if not p.exists():
                sys.exit(f"ERROR: File not found: {p}")
            file_paths = [p]
        else:
            sys.exit("No file selected.")

    # ── 1. Read all selected files and merge into one lookup ──────────────────
    file_map: dict[str, tuple[str, date | None, str]] = {}  # pk → (name, date, source_file)
    total_rows = 0
    bad_dates  = 0
    no_phone   = 0

    for file_path in file_paths:
        print(f"\n📄 Reading: {file_path.name}")
        try:
            rows = read_file(file_path)
        except Exception as e:
            print(f"  ⚠️  Skipped — could not read: {e}")
            continue
        if not rows:
            print("  ⚠️  Skipped — empty file.")
            continue

        headers   = list(rows[0].keys())
        phone_col = detect_col(headers, PHONE_ALIASES)
        date_col  = detect_col(headers, DATE_ALIASES)
        name_col  = detect_col(headers, NAME_ALIASES)

        if not phone_col:
            print(f"  ⚠️  Skipped — no phone column found. Columns: {headers}")
            continue
        if not date_col:
            print(f"  ⚠️  Skipped — no date column found. Columns: {headers}")
            continue

        print(f"  Rows: {len(rows)}  |  phone='{phone_col}'  date='{date_col}'" +
              (f"  name='{name_col}'" if name_col else ""))

        file_rows = 0
        for row in rows:
            raw_phone = str(row.get(phone_col, "") or "")
            pk = phone_key(raw_phone)
            if not pk:
                no_phone += 1
                continue
            d = parse_date(row.get(date_col))
            if d is None:
                bad_dates += 1
            name = str(row.get(name_col, "") or "").strip() if name_col else ""
            # First file wins if same phone appears in multiple files
            if pk not in file_map:
                file_map[pk] = (name, d, file_path.name)
            file_rows += 1
        total_rows += file_rows

    if not file_map:
        sys.exit("ERROR: No usable rows found across all selected files.")

    print(f"\n  Total usable rows : {total_rows}")
    if bad_dates:
        print(f"  ⚠️  Unreadable dates  : {bad_dates} (will show as '???')")
    if no_phone:
        print(f"  ⚠️  No/short phone    : {no_phone} rows skipped")

    # ── 2. Fetch DB leads ─────────────────────────────────────────────────────
    print(f"\n🗄️  Fetching leads from schema '{args.schema}'...")
    leads = fetch_leads(cfg, args.schema)
    print(f"  Leads in DB: {len(leads)}")

    # ── 3. Compare ────────────────────────────────────────────────────────────
    matched   = []   # (lead, file_name, file_date, db_date, same)
    db_only   = []   # leads in DB not found in file
    file_only = []   # keys in file not found in DB

    db_keys = {}
    for lead in leads:
        pk = lead.get("phone_key") or phone_key(lead.get("phone", "") or "")
        if pk:
            db_keys[pk] = lead

    for pk, (fname, fdate, src_file) in file_map.items():
        lead = db_keys.get(pk)
        if lead:
            db_dt = lead["created_at"]
            db_date = db_dt.date() if isinstance(db_dt, datetime) else db_dt
            same = (fdate is not None and db_date == fdate)
            matched.append((lead, fname, fdate, db_date, same, src_file))
        else:
            file_only.append((pk, fname, fdate, src_file))

    for pk, lead in db_keys.items():
        if pk not in file_map:
            db_only.append(lead)

    mismatches = [(l, fn, fd, dd, sf) for l, fn, fd, dd, same, sf in matched if not same]

    # ── 4. Report ─────────────────────────────────────────────────────────────
    COL_W = 14
    FILE_W = 24

    def row_line(lead_id, name, phone, db_d, file_d, flag, src=""):
        return (
            f"  {str(lead_id):<6}  "
            f"{name[:20]:<20}  "
            f"{phone or '':<14}  "
            f"{str(db_d):<{COL_W}}  "
            f"{str(file_d or '???'):<{COL_W}}  "
            f"{flag:<12}  "
            f"{src[:FILE_W]}"
        )

    header = (
        f"  {'ID':<6}  {'Name':<20}  {'Phone':<14}  "
        f"{'DB date':<{COL_W}}  {'File date':<{COL_W}}  {'Status':<12}  Source file"
    )
    divider = "  " + "-" * (len(header) - 2)

    print(f"\n{'='*80}")
    print(f" MATCHED: {len(matched)} leads found in both DB and files")
    print(f"{'='*80}")
    print(header)
    print(divider)
    for lead, fname, fdate, db_date, same, src_file in matched:
        flag = "✓ same" if same else "✗ MISMATCH"
        pk = lead.get("phone_key") or ""
        print(row_line(lead["id"], fname or lead.get("first_name") or "", pk, db_date, fdate, flag, src_file))

    if db_only:
        print(f"\n{'='*80}")
        print(f" DB ONLY: {len(db_only)} leads in DB not found in any file")
        print(f"{'='*80}")
        for lead in db_only[:20]:
            db_dt = lead["created_at"]
            db_date = db_dt.date() if isinstance(db_dt, datetime) else db_dt
            pk = lead.get("phone_key") or ""
            print(f"  id={lead['id']:<6} {lead.get('first_name') or '':<20} {pk:<14} DB={db_date}")
        if len(db_only) > 20:
            print(f"  ... and {len(db_only) - 20} more")

    if file_only:
        print(f"\n{'='*80}")
        print(f" FILE ONLY: {len(file_only)} rows in files not matched in DB")
        print(f"{'='*80}")
        for pk, fname, fdate, src_file in file_only[:20]:
            print(f"  phone_key={pk:<14} name={fname or '—':<20} date={fdate}  [{src_file}]")
        if len(file_only) > 20:
            print(f"  ... and {len(file_only) - 20} more")

    print(f"\n{'='*80}")
    print(" SUMMARY")
    print(f"{'='*80}")
    print(f"  Files scanned        : {len(file_paths)}")
    print(f"  File rows read       : {total_rows}")
    print(f"  Matched (by phone)   : {len(matched)}")
    print(f"    ✓ date same        : {len(matched) - len(mismatches)}")
    print(f"    ✗ date mismatch    : {len(mismatches)}")
    print(f"  DB only (no file row): {len(db_only)}")
    print(f"  File only (not in DB): {len(file_only)}")
    print(f"{'='*80}\n")

    if not mismatches:
        print("  ✓ All matched leads already have the correct date. Nothing to fix.")
        return

    # ── 5. SQL / apply ────────────────────────────────────────────────────────
    updates = [(l["id"], fd) for l, fn, fd, dd, sf in mismatches if fd is not None]

    if args.show_sql or args.apply:
        print(f"\n-- SQL to fix {len(updates)} mismatches --")
        print(f"SET search_path TO {args.schema};\n")
        for lead_id, fdate in updates:
            print(f"UPDATE leads SET created_at = '{fdate} 00:00:00+00' WHERE id = {lead_id};")
        print()

    if args.apply:
        if not updates:
            print("  Nothing to apply (all mismatches have no valid file date).")
            return
        print(f"Applying {len(updates)} UPDATEs to schema '{args.schema}'...")
        n = apply_updates(cfg, args.schema, updates)
        print(f"✓ Done — {n} row(s) updated.")
    elif mismatches:
        print(
            f"  {len(updates)} rows need fixing.\n"
            f"  Run with --show-sql to print the UPDATE statements.\n"
            f"  Run with --apply to write the changes to the DB."
        )


if __name__ == "__main__":
    main()
