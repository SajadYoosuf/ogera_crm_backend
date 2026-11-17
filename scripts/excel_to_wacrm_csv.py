"""
excel_to_wacrm_csv.py

Converts all Ventino contact Excel files into a single WACRM-compatible CSV.

WACRM requires:
  - phone in E.164 format (+91XXXXXXXXXX or +971XXXXXXXXX)
  - CSV columns: phone, name, email, company, tags
  - tags: comma-separated tag names (auto-created on import)

Sources and their tags:
  CUSTOMERS.xlsx          -> "General Enquiry"
  buyers_register.xlsx    -> "Buyer"
  FORM AS  2.xlsx         -> "Form Lead"
  Onam Offers.xlsx        -> "Onam Ad Lead"
  Monsoon Offer.xlsx      -> "Monsoon Ad Lead"
  Monsoon Offer Aug26     -> "Monsoon Aug26 Ad Lead"

Contacts appearing in multiple files get all applicable tags merged.
Outputs:
  wacrm_import.csv  - ready to upload in WACRM → Contacts → Import
  wacrm_skipped.csv - rows with no valid phone (for manual follow-up)
"""

import csv
import os
import re
import sys

import openpyxl

# ── Paths ────────────────────────────────────────────────────────────────────

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_CSV     = os.path.join(BACKEND_DIR, "scripts", "wacrm_import.csv")
SKIP_CSV    = os.path.join(BACKEND_DIR, "scripts", "wacrm_skipped.csv")

SOURCES = [
    {
        "file":  os.path.join(BACKEND_DIR, "CUSTOMERS.xlsx"),
        "label": "CUSTOMERS",
        "tags":  ["General Enquiry"],
        "cols":  {"name": "NAME ", "phone": "NUMBER", "location": "LOCATION"},
        "type":  "customers",
    },
    {
        "file":  os.path.join(BACKEND_DIR, "buyers_register.xlsx"),
        "label": "BUYERS",
        "tags":  ["Buyer"],
        "cols":  {"name": "Name", "phone": "Phone", "alt_phone": "Alt Phone", "raw": "Raw Mobile", "address": "Address"},
        "type":  "buyers",
    },
    {
        "file":  os.path.join(BACKEND_DIR, "FORM AS  2.xlsx"),
        "label": "FORM",
        "tags":  ["Form Lead"],
        "cols":  {"name": "name", "phone": "number"},
        "type":  "form",
    },
    {
        "file":  os.path.join(BACKEND_DIR, "Onam Offers.xlsx"),
        "label": "ONAM",
        "tags":  ["Onam Ad Lead"],
        "cols":  {"name": "full_name", "phone": "phone_number", "address": "street_address", "city": "city"},
        "type":  "ad_lead",
    },
    {
        "file":  os.path.join(BACKEND_DIR, "Monsoon Offer.xlsx"),
        "label": "MONSOON",
        "tags":  ["Monsoon Ad Lead"],
        "cols":  {"name": "full_name", "phone": "phone_number", "address": "street_address", "city": "city"},
        "type":  "ad_lead",
    },
    {
        "file":  os.path.join(BACKEND_DIR, "Monsoon Offer Aug 2026.xlsx"),
        "label": "MONSOON_AUG26",
        "tags":  ["Monsoon Aug26 Ad Lead"],
        "cols":  {"name": "full_name", "phone": "phone_number", "address": "street_address", "city": "city"},
        "type":  "ad_lead",
    },
]

# ── Phone normalization ───────────────────────────────────────────────────────

def normalize_phone(raw) -> str | None:
    """
    Convert any phone value to E.164 format (+countrycode + digits).
    Returns None if the number is invalid or a test placeholder.

    Rules:
      - Strip Facebook's "p:" prefix
      - Skip test lead placeholders
      - 10-digit Indian number (starts 6-9) → +91XXXXXXXXXX
      - Already has +91 → keep
      - Already has +971 (UAE) → keep
      - Anything else → None (invalid)
    """
    if raw is None:
        return None

    s = str(raw).strip()

    # Facebook export prefix
    if s.lower().startswith("p:"):
        s = s[2:].strip()

    # Test lead placeholder
    if "<" in s or "test" in s.lower() or "dummy" in s.lower():
        return None

    # Remove common formatting
    s = s.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

    # Already E.164 with country code
    if s.startswith("+91") and re.fullmatch(r"\+91[6-9]\d{9}", s):
        return s
    if s.startswith("+971") and re.fullmatch(r"\+971\d{9}", s):
        return s
    if s.startswith("+") and len(s) > 10:
        # Other international — keep if digits look plausible
        digits = s[1:]
        if re.fullmatch(r"\d{10,15}", digits):
            return s
        return None

    # GCC numbers stored without + (971=UAE, 965=Kuwait, 973=Bahrain, 974=Qatar, 966=Saudi)
    # Format: countrycode + mobile digits (total 11-12 digits)
    gcc_match = re.fullmatch(r"(971\d{9}|965\d{8}|973\d{8}|974\d{8}|966\d{9})", s)
    if gcc_match:
        return f"+{s}"

    # Strip leading 0 or 91 country prefix
    if s.startswith("0") and len(s) == 11:
        s = s[1:]
    if s.startswith("91") and len(s) == 12:
        s = s[2:]

    # 10-digit Indian mobile
    if re.fullmatch(r"[6-9]\d{9}", s):
        return f"+91{s}"

    return None


def phone_key(e164: str) -> str:
    """Digits-only key for deduplication, matching WACRM's phone_normalized."""
    return re.sub(r"\D", "", e164)


# ── Location helpers ──────────────────────────────────────────────────────────

# Top locations in CUSTOMERS.xlsx, normalised to consistent Title Case
LOCATION_MAP = {
    "uragam": "Uragam", "valiyangadi": "Valiyangadi", "kozhikode": "Kozhikode",
    "kannur": "Kannur", "panakkad": "Panakkad", "malappuram": "Malappuram",
    "thrissur": "Thrissur", "perinthalmanna": "Perinthalmanna", "coimbatore": "Coimbatore",
    "vengara": "Vengara", "kanhangad": "Kanhangad", "kochi": "Kochi",
    "calicut": "Kozhikode", "muvattupuzha": "Muvattupuzha", "kasargode": "Kasargod",
    "thodupuzha": "Thodupuzha", "kottayam": "Kottayam", "pattambi": "Pattambi",
    "mampattumoola": "Mampattumoola", "nilambur": "Nilambur", "tirur": "Tirur",
    "tirurangadi": "Tirurangadi",
}

def normalise_location(raw) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    return LOCATION_MAP.get(s.lower(), s.title())


def city_from_address(address) -> str:
    """Extract last meaningful token from a comma-separated address as city."""
    if not address:
        return ""
    parts = [p.strip() for p in str(address).split(",") if p.strip()]
    # Last part is often a PIN code — skip it
    for part in reversed(parts):
        if re.fullmatch(r"\d{6}", part):
            continue
        return part.title()
    return ""


# ── Main merge logic ──────────────────────────────────────────────────────────

def load_rows(source: dict) -> tuple[list[dict], list[dict]]:
    """
    Load one Excel source, return (valid_rows, skipped_rows).
    Each valid row: {"phone": E164, "name": str, "company": str, "tags": set}
    Each skipped row: {"raw_phone": str, "name": str, "source": str, "reason": str}
    """
    path = source["file"]
    label = source["label"]
    tags = source["tags"]
    src_type = source["type"]
    col_map = source["cols"]

    if not os.path.exists(path):
        print(f"  [WARN] File not found: {path}")
        return [], []

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows_raw = list(ws.iter_rows(values_only=True))
    wb.close()

    if not rows_raw:
        return [], []

    # Build header → index map (case-insensitive strip)
    header = {str(c).strip().lower() if c else "": i for i, c in enumerate(rows_raw[0])}

    def get(row, col_key):
        col_name = col_map.get(col_key, "").strip().lower()
        idx = header.get(col_name)
        if idx is None or idx >= len(row):
            return None
        v = row[idx]
        return str(v).strip() if v is not None else None

    valid, skipped = [], []

    for row in rows_raw[1:]:
        raw_phone = get(row, "phone")
        name = get(row, "name") or ""
        name = name.title() if name else ""

        # Determine company value based on source type
        company = ""
        if src_type == "customers":
            company = normalise_location(get(row, "location"))
        elif src_type == "buyers":
            company = city_from_address(get(row, "address"))
        elif src_type == "ad_lead":
            company = normalise_location(get(row, "city")) or city_from_address(get(row, "address"))

        phone = normalize_phone(raw_phone)

        if not phone:
            reason = "missing" if not raw_phone or raw_phone == "None" else "invalid format"
            skipped.append({
                "raw_phone": raw_phone or "",
                "name": name,
                "source": label,
                "reason": reason,
            })
            continue

        valid.append({
            "phone": phone,
            "name": name,
            "company": company,
            "tags": set(tags),
        })

    return valid, skipped


def merge_all_sources() -> tuple[dict, list[dict]]:
    """
    Load all sources, merge by phone_key.
    Returns (contacts_dict, all_skipped).
    contacts_dict keyed by phone_normalized digits.
    """
    contacts: dict[str, dict] = {}
    all_skipped: list[dict] = []

    for source in SOURCES:
        label = source["label"]
        print(f"Loading {label} ...", end=" ", flush=True)
        valid, skipped = load_rows(source)
        print(f"{len(valid)} valid, {len(skipped)} skipped")
        all_skipped.extend(skipped)

        for row in valid:
            key = phone_key(row["phone"])
            if key in contacts:
                # Merge: keep best name, union tags, keep first company if blank
                existing = contacts[key]
                if not existing["name"] and row["name"]:
                    existing["name"] = row["name"]
                if not existing["company"] and row["company"]:
                    existing["company"] = row["company"]
                existing["tags"] |= row["tags"]
            else:
                contacts[key] = {
                    "phone": row["phone"],
                    "name": row["name"],
                    "company": row["company"],
                    "tags": row["tags"],
                }

    return contacts, all_skipped


# ── Output ────────────────────────────────────────────────────────────────────

def write_import_csv(contacts: dict):
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["phone", "name", "email", "company", "tags"])
        writer.writeheader()
        for contact in contacts.values():
            writer.writerow({
                "phone": contact["phone"],
                "name": contact["name"],
                "email": "",
                "company": contact["company"],
                "tags": ",".join(sorted(contact["tags"])),
            })


def write_skipped_csv(skipped: list[dict]):
    if not skipped:
        return
    with open(SKIP_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["source", "name", "raw_phone", "reason"])
        writer.writeheader()
        writer.writerows(skipped)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    print("\n=== WACRM Contact Import Converter ===\n")

    contacts, skipped = merge_all_sources()

    # Stats
    multi_tag = sum(1 for c in contacts.values() if len(c["tags"]) > 1)
    tag_counts: dict[str, int] = {}
    for c in contacts.values():
        for t in c["tags"]:
            tag_counts[t] = tag_counts.get(t, 0) + 1

    print(f"\n--- Results ---")
    print(f"Unique contacts (valid phone): {len(contacts)}")
    print(f"Skipped (no valid phone):      {len(skipped)}")
    print(f"Contacts with 2+ tags:         {multi_tag}")
    print(f"\nTag breakdown:")
    for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1]):
        print(f"  {tag:<30} {count}")

    write_import_csv(contacts)
    write_skipped_csv(skipped)

    print(f"\nOutput:")
    print(f"  Import CSV : {OUT_CSV}")
    if skipped:
        print(f"  Skipped CSV: {SKIP_CSV}")

    print(f"\nNext step: Open WACRM → Contacts → Import and upload wacrm_import.csv")
    print()


if __name__ == "__main__":
    main()
