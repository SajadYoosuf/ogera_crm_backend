"""
Normalise the Meta lead exports, CUSTOMERS.xlsx and the FORM AS buyer registers
(xlsx + pdf). Leads go to a JSON that scripts/import_excel_leads.py can load; the buyer
registers (sales-side data) go to a separate JSON and are not imported into the CRM.

Needs openpyxl and pdfplumber (not in requirements.txt):  pip install openpyxl pdfplumber
Run from project root:
    python scripts/prepare_excel_leads.py leads_prepared.json buyers_register.json
"""

import datetime
import json
import re
import sys
from collections import Counter

import openpyxl
import pdfplumber

META_FILES = ["Monsoon Offer.xlsx", "Monsoon Offer Aug 2026.xlsx", "Onam Offers.xlsx"]
CUSTOMERS_FILE = "CUSTOMERS.xlsx"
BUYER_XLSX = "FORM AS  2.xlsx"
BUYER_PDF = "FORM AS (Autosaved) (1).pdf"

GULF_PREFIXES = ("971", "966", "968", "974", "965", "973")
TANK_FOR = {
    "for_my_new_house": ("Under Construction", None),
    "for_changing_from_plastic_tank": (None, "Pvc"),
    "for_changing_from_concrete_tank": (None, "Concrete"),
}


def clean(v):
    if v is None:
        return None
    v = re.sub(r"\s+", " ", str(v)).strip()
    return v or None


def parse_phones(raw) -> list[str]:
    """Return every phone found in a cell as +<digits>. Invalid fragments are dropped."""
    if raw is None:
        return []
    text = str(raw).replace("p:", " ")
    out = []
    # One cell can hold several numbers on separate lines / separated by , or /
    for tok in re.findall(r"\+?\d[\d \-]{6,}\d", text):
        plus = tok.startswith("+")
        d = re.sub(r"\D", "", tok)
        if plus:
            out.append("+" + d)
        elif len(d) == 10:
            out.append("+91" + d)
        elif len(d) == 12 and d.startswith("91"):
            out.append("+" + d)
        elif len(d) == 11 and d.startswith("0"):
            out.append("+91" + d[1:])
        elif d.startswith(GULF_PREFIXES) and len(d) >= 11:
            out.append("+" + d)
    return out


def key(phone: str) -> str:
    return phone[-10:]


def parse_dmy(s) -> str | None:
    s = clean(s)
    if not s:
        return None
    m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", s)
    if not m:
        return None
    d, mo, y = map(int, m.groups())
    return datetime.date(y, mo, d).isoformat()


def title(s):
    return s.title() if s and (s.isupper() or s.islower()) else s


def read_xlsx(path):
    ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "leads_prepared.json"
    buyers_path = sys.argv[2] if len(sys.argv) > 2 else "buyers_register.json"
    buyers_out = []  # sales-side data: kept out of the CRM, saved for later
    records: dict[str, dict] = {}
    warnings = Counter()
    unparsed = []

    def merge(rec):
        k = key(rec["phone"])
        cur = records.get(k)
        if cur is None:
            records[k] = rec
            return
        warnings["duplicate_phone_merged"] += 1
        # Richer record wins; fill gaps from the other one.
        if rec["origin"] == "meta" and cur["origin"] != "meta":
            cur, rec = rec, cur
            records[k] = cur
        for f, v in rec.items():
            if f == "origin":
                continue
            if cur.get(f) in (None, "", "Unknown") and v:
                cur[f] = v

    # --- Meta lead ads -------------------------------------------------
    for f in META_FILES:
        rows = read_xlsx(f)
        h = {name: i for i, name in enumerate(rows[0])}
        for r in rows[1:]:
            if not any(r):
                continue
            name = clean(r[h["full_name"]])
            if name and "test lead" in name:
                warnings["meta_test_lead_skipped"] += 1
                continue
            phones = parse_phones(r[h["phone_number"]])
            if not phones:
                warnings["meta_no_valid_phone"] += 1
                unparsed.append([f, name, r[h["phone_number"]]])
                continue
            created = datetime.datetime.fromisoformat(r[h["created_time"]])
            created_utc = created.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            house, tank = TANK_FOR.get(r[h["i_need_my_ventino_stainless_steel_water_tank_for"]], (None, None))
            plat = {"fb": "Facebook", "ig": "Instagram"}.get(r[h["platform"]], r[h["platform"]])
            street, city = clean(r[h["street_address"]]), clean(r[h["city"]])
            merge({
                "origin": "meta",
                "name": (name or "Unknown")[:150],
                "phone": phones[0],
                "phone_alt": phones[1] if len(phones) > 1 else None,
                "district": title(city),
                "address": street or city,
                "source": "meta",
                "created_at": created_utc.isoformat(sep=" "),
                "house_stage": house,
                "current_tank_type": tank,
                "notes": (
                    f"Meta lead ad | Campaign: {clean(r[h['campaign_name']])} | "
                    f"Form: {clean(r[h['form_name']])} | Platform: {plat} | Meta ID: {r[h['id']]}"
                ),
            })

    # --- CUSTOMERS.xlsx (NAME, NUMBER, LOCATION) -----------------------
    for r in read_xlsx(CUSTOMERS_FILE)[1:]:
        phones = parse_phones(r[1])
        if not phones:
            warnings["customers_no_valid_phone"] += 1
            continue
        loc = clean(r[2])
        merge({
            "origin": "customers",
            "name": (clean(r[0]) or "Unknown")[:150],
            "phone": phones[0],
            "district": title(loc),
            "address": loc,
            "source": "offline",
            "notes": "Imported from CUSTOMERS.xlsx",
        })

    # --- Buyer registers (FORM AS): sales-side data, saved to its own JSON ---
    buyer_rows = []
    for r in read_xlsx(BUYER_XLSX)[1:]:
        if r[1] and r[2]:
            buyer_rows.append((r[0], r[1], r[2], r[3]))
        elif r[1] or r[2]:
            warnings["buyer_xlsx_incomplete_row"] += 1
            unparsed.append([BUYER_XLSX, r[1], r[2]])
    with pdfplumber.open(BUYER_PDF) as pdf:
        for pg in pdf.pages:
            for t in pg.extract_tables():
                for r in t:
                    if r and r[0] and str(r[0]).strip().isdigit():
                        buyer_rows.append((r[1], r[2], r[3], r[4]))
    for date, name, mobile, addr in buyer_rows:
        # The PDF sometimes drops the first digit(s) of the mobile onto the end of the name
        m = re.fullmatch(r"(.*?)(\d+)", clean(name) or "")
        md = re.sub(r"\D", "", str(mobile or ""))
        if m and md and len(md) < 10 and len(m.group(2)) + len(md) == 10:
            name, mobile = m.group(1), m.group(2) + md
        phones = parse_phones(mobile)
        if not phones:
            warnings["buyer_no_valid_phone"] += 1
        buyers_out.append({
            "purchase_date": parse_dmy(date),
            "name": clean(name),
            "phone": phones[0] if phones else None,
            "phone_alt": phones[1] if len(phones) > 1 else None,
            "raw_mobile": clean(mobile),
            "address": clean(addr),
        })

    data = sorted(records.values(), key=lambda x: x["phone"])
    json.dump(data, open(out_path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    json.dump(buyers_out, open(buyers_path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("lead records:", len(data), dict(Counter(x["origin"] for x in data)))
    print("buyer register rows:", len(buyers_out), "without phone:", sum(1 for b in buyers_out if not b["phone"]))
    print("warnings:", dict(warnings))
    print("unparsed rows:", unparsed)


if __name__ == "__main__":
    main()
