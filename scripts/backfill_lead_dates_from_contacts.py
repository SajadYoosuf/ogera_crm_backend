"""
backfill_lead_dates_from_contacts.py
=====================================
Matches each lead in PostgreSQL against a contacts JSON export by phone number.
If the contact's created_at is OLDER than the lead's created_at, the lead's
created_at is updated to the contact's date.

This restores the original lead dates after a bulk import gave all leads today's
timestamp. Only rows where the JSON date is strictly earlier are touched — leads
that already have the correct (older) date are skipped.

HOW TO RUN:
    cd ogera_crm_backend

    # Dry run — see what would change without touching the DB:
    python scripts/backfill_lead_dates_from_contacts.py contacts_2026-09-30.json

    # Different schema (default: ventino):
    python scripts/backfill_lead_dates_from_contacts.py contacts_2026-09-30.json --schema ogera

    # Apply changes to DB:
    python scripts/backfill_lead_dates_from_contacts.py contacts_2026-09-30.json --apply

MATCHING RULE:
    Lead.phone_key (last 10 digits) == Contact.phone[-10:]
    If multiple contacts share a phone, the one with the earliest created_at wins.

UPDATE RULE:
    Only update if  contact.created_at < lead.created_at
    (i.e. the JSON has an older/original date that should replace the current one)
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Load .env ─────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    sys.exit("ERROR: psycopg2 not installed.  Run: pip install psycopg2-binary")


# ── DB ────────────────────────────────────────────────────────────────────────

def get_db_config() -> dict:
    missing = [k for k in ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
                           "POSTGRES_USER", "POSTGRES_PASSWORD") if not os.getenv(k)]
    if missing:
        sys.exit(f"ERROR: Missing env vars: {', '.join(missing)}")
    return {
        "host":     os.environ["POSTGRES_HOST"],
        "port":     int(os.environ["POSTGRES_PORT"]),
        "dbname":   os.environ["POSTGRES_DB"],
        "user":     os.environ["POSTGRES_USER"],
        "password": os.environ["POSTGRES_PASSWORD"],
    }


def fetch_leads(cfg: dict, schema: str) -> list[dict]:
    conn = psycopg2.connect(**cfg)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SET search_path TO {schema}")
            cur.execute("""
                SELECT id, first_name, last_name, phone, phone_key, created_at
                FROM   leads
                WHERE  deleted_at IS NULL
                ORDER  BY id
            """)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def apply_updates(cfg: dict, schema: str, updates: list[tuple[int, datetime]]) -> int:
    conn = psycopg2.connect(**cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {schema}")
            count = 0
            for lead_id, new_dt in updates:
                cur.execute(
                    "UPDATE leads SET created_at = %s WHERE id = %s",
                    (new_dt, lead_id),
                )
                count += cur.rowcount
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Phone normalisation ───────────────────────────────────────────────────────

def phone_key(raw: str) -> str | None:
    digits = re.sub(r"\D", "", str(raw or ""))
    return digits[-10:] if len(digits) >= 10 else None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Backfill lead created_at from a contacts JSON export (only sets older dates)."
    )
    parser.add_argument("json_file", help="Path to the contacts JSON export (e.g. contacts_2026-09-30.json)")
    parser.add_argument("--schema", default="ventino", help="Tenant schema (default: ventino)")
    parser.add_argument("--apply", action="store_true", help="Write changes to DB (default: dry run)")
    args = parser.parse_args()

    json_path = Path(args.json_file)
    if not json_path.exists():
        sys.exit(f"ERROR: File not found: {json_path}")

    # ── 1. Load contacts JSON ─────────────────────────────────────────────────
    print(f"\n📄 Loading contacts from: {json_path.name}")
    with open(json_path, encoding="utf-8") as f:
        raw = json.load(f)
    contacts = raw if isinstance(raw, list) else raw.get("data") or raw.get("contacts") or []
    print(f"   {len(contacts)} contact records")

    # Build phone_key → earliest created_at map
    # (if two contacts share a phone, use the older date)
    contact_map: dict[str, datetime] = {}
    skipped_no_phone = 0
    skipped_no_date  = 0

    for c in contacts:
        pk = phone_key(str(c.get("phone", "") or ""))
        if not pk:
            skipped_no_phone += 1
            continue
        raw_dt = c.get("created_at")
        if not raw_dt:
            skipped_no_date += 1
            continue
        # Parse "2026-07-29 09:58:05" or ISO formats
        try:
            if isinstance(raw_dt, str):
                dt = datetime.strptime(raw_dt.strip(), "%Y-%m-%d %H:%M:%S")
            elif isinstance(raw_dt, datetime):
                dt = raw_dt
            else:
                skipped_no_date += 1
                continue
        except ValueError:
            try:
                dt = datetime.fromisoformat(str(raw_dt))
            except ValueError:
                skipped_no_date += 1
                continue

        # Keep the earliest date for this phone key
        if pk not in contact_map or dt < contact_map[pk]:
            contact_map[pk] = dt

    print(f"   Unique phone keys with dates: {len(contact_map)}")
    if skipped_no_phone:
        print(f"   ⚠️  Skipped (no/short phone): {skipped_no_phone}")
    if skipped_no_date:
        print(f"   ⚠️  Skipped (no date):        {skipped_no_date}")

    # ── 2. Fetch leads from DB ────────────────────────────────────────────────
    cfg = get_db_config()
    print(f"\n🗄️  Fetching leads from schema '{args.schema}'...")
    leads = fetch_leads(cfg, args.schema)
    print(f"   {len(leads)} leads in DB")

    # ── 3. Compare and decide what to update ─────────────────────────────────
    to_update   = []   # (lead_id, lead_name, phone_key, db_dt, contact_dt)
    already_ok  = []   # lead already has the correct or older date
    no_match    = []   # lead phone not found in contacts JSON

    for lead in leads:
        pk = lead.get("phone_key") or phone_key(lead.get("phone", "") or "")
        if not pk:
            no_match.append(lead)
            continue

        contact_dt = contact_map.get(pk)
        if contact_dt is None:
            no_match.append(lead)
            continue

        db_dt = lead["created_at"]
        # Normalise to naive datetime for comparison
        if hasattr(db_dt, "tzinfo") and db_dt.tzinfo is not None:
            db_dt_naive = db_dt.replace(tzinfo=None)
        else:
            db_dt_naive = db_dt

        name = f"{lead.get('first_name') or ''} {lead.get('last_name') or ''}".strip()

        if contact_dt < db_dt_naive:
            # JSON date is older → needs update
            to_update.append((lead["id"], name, pk, db_dt_naive, contact_dt))
        else:
            # DB date is already equal or older → fine
            already_ok.append((lead["id"], name, pk, db_dt_naive, contact_dt))

    # ── 4. Report ─────────────────────────────────────────────────────────────
    COL = 14

    def fmt_row(lead_id, name, pk, db_d, json_d, action):
        return (
            f"  {str(lead_id):<6}  {name[:22]:<22}  {pk:<13}  "
            f"{str(db_d.date() if hasattr(db_d,'date') else db_d):<{COL}}  "
            f"{str(json_d.date() if hasattr(json_d,'date') else json_d):<{COL}}  "
            f"{action}"
        )

    hdr = (f"  {'ID':<6}  {'Name':<22}  {'Phone key':<13}  "
           f"{'DB date':<{COL}}  {'JSON date':<{COL}}  Action")
    div = "  " + "-" * (len(hdr) - 2)

    if to_update:
        print(f"\n{'='*80}")
        print(f" WILL UPDATE ({len(to_update)} leads — JSON date is older than DB date)")
        print(f"{'='*80}")
        print(hdr)
        print(div)
        for lid, name, pk, db_dt, json_dt in to_update:
            print(fmt_row(lid, name, pk, db_dt, json_dt, "→ UPDATE"))

    if already_ok:
        print(f"\n{'='*80}")
        print(f" ALREADY CORRECT ({len(already_ok)} leads — DB date is already old enough)")
        print(f"{'='*80}")
        print(hdr)
        print(div)
        for lid, name, pk, db_dt, json_dt in already_ok[:20]:
            print(fmt_row(lid, name, pk, db_dt, json_dt, "✓ skip"))
        if len(already_ok) > 20:
            print(f"  ... and {len(already_ok) - 20} more")

    if no_match:
        print(f"\n{'='*80}")
        print(f" NO MATCH ({len(no_match)} leads — phone not found in JSON)")
        print(f"{'='*80}")
        for lead in no_match[:10]:
            pk = lead.get("phone_key") or ""
            db_dt = lead["created_at"]
            name = f"{lead.get('first_name') or ''} {lead.get('last_name') or ''}".strip()
            print(f"  id={lead['id']:<6} {name:<22} phone_key={pk}  DB={db_dt}")
        if len(no_match) > 10:
            print(f"  ... and {len(no_match) - 10} more")

    print(f"\n{'='*80}")
    print(" SUMMARY")
    print(f"{'='*80}")
    print(f"  Leads in DB          : {len(leads)}")
    print(f"  Matched to JSON      : {len(to_update) + len(already_ok)}")
    print(f"    → Will update      : {len(to_update)}  (JSON date is older)")
    print(f"    → Already correct  : {len(already_ok)}  (DB date already old enough)")
    print(f"  No phone match       : {len(no_match)}")
    print(f"{'='*80}")

    if not to_update:
        print("\n  ✓ Nothing to update.")
        return

    # ── 5. Apply ──────────────────────────────────────────────────────────────
    if args.apply:
        print(f"\nApplying {len(to_update)} updates to schema '{args.schema}'...")
        updates = [(lid, json_dt) for lid, name, pk, db_dt, json_dt in to_update]
        n = apply_updates(cfg, args.schema, updates)
        print(f"✓ Done — {n} row(s) updated.")
    else:
        print(
            f"\n  Dry run — no changes made.\n"
            f"  Run with --apply to write {len(to_update)} update(s) to the DB."
        )


if __name__ == "__main__":
    main()
