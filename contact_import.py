"""Contact import: read a CSV/XLSX sheet whose layout may be messy, validate every row and report.

`analyze` never writes. `run_import` works on the same analysis, so the preview and the result
cannot disagree. A row is `ready`, `warning` (imported with a fix-up), `duplicate` (already in the
CRM) or `error` (skipped, with a reason).
"""

import csv
import datetime
import io
import random
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from models import ConsumerProfile, Contact, LeadSource, LeadStage, PartnerProfile
from schemas import ProfessionType

MAX_ROWS = 5000
MAX_BYTES = 5 * 1024 * 1024
HEADER_SCAN_ROWS = 10
MISSING_DISTRICT = "Unknown"  # contacts.district is NOT NULL

FIELDS = {
    "name": "Name",
    "phone": "Phone",
    "phone_alt": "Alternate phone",
    "district": "District",
    "address": "Address",
    "contact_type": "Type",
    "lead_source": "Lead source",
    "profession_type": "Profession (partners)",
    "house_stage": "House stage (consumers)",
    "work_area": "Work area (partners)",
    "notes": "Notes",
    "created_at": "Created date",
}

# Header text (lower-cased, letters and digits only) -> field
ALIASES = {
    "name": ["name", "fullname", "customername", "contactname", "buyername", "clientname", "customer"],
    "phone": ["phone", "phoneno", "phonenumber", "mobile", "mobileno", "mobilenumber", "contact",
              "contactno", "contactnumber", "whatsapp", "whatsappnumber", "cell", "tel", "telephone"],
    "phone_alt": ["phonealt", "altphone", "alternatephone", "alternatenumber", "secondaryphone",
                  "phone2", "mobile2", "altmobile"],
    "district": ["district", "city", "place", "location", "area", "panchayath", "town"],
    "address": ["address", "fulladdress", "addr", "streetaddress"],
    "contact_type": ["type", "contacttype", "category", "customertype"],
    "lead_source": ["source", "leadsource", "sourceofsale", "origin", "channel"],
    "profession_type": ["profession", "professiontype", "occupation", "trade", "partnertype"],
    "house_stage": ["housestage", "constructionstage", "buildingstage"],
    "work_area": ["workarea", "servicearea", "operatingarea"],
    "notes": ["notes", "note", "remarks", "remark", "comments", "comment", "description"],
    "created_at": ["createdat", "createddate", "date", "created", "enquirydate", "saledate"],
}
_ALIAS_TO_FIELD = {alias: f for f, aliases in ALIASES.items() for alias in aliases}

TEMPLATE_HEADERS = ["Name", "Phone", "Alternate phone", "District", "Address", "Type",
                    "Lead source", "Profession", "House stage", "Work area", "Notes", "Created date"]
TEMPLATE_EXAMPLE = ["Asha Nair", "9876543210", "", "Kozhikode", "12 MG Road", "consumer",
                    "Instagram", "", "Under Construction", "", "Wants a 1000 L tank", "2026-10-01"]


class ImportFileError(ValueError):
    """The file as a whole cannot be read (wrong type, too big, empty)."""


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))  # Excel stores 9876543210 as 9876543210.0
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value).strip()


def read_rows(filename: str, content: bytes) -> list[list[str]]:
    """Every non-blank row of the first sheet as text cells."""
    if len(content) > MAX_BYTES:
        raise ImportFileError("File is larger than 5 MB")
    name = (filename or "").lower()
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook

        try:
            sheet = load_workbook(io.BytesIO(content), read_only=True, data_only=True).worksheets[0]
            rows = [[_cell(c) for c in row] for row in sheet.iter_rows(values_only=True)]
        except Exception as exc:  # corrupt or password-protected workbook
            raise ImportFileError("Could not read this Excel file") from exc
    elif name.endswith((".csv", ".txt")):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("cp1252", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = [[_cell(c) for c in row] for row in csv.reader(io.StringIO(text), dialect)]
    else:
        raise ImportFileError("Upload a .csv or .xlsx file")
    rows = [r for r in rows if any(r)]
    if not rows:
        raise ImportFileError("The file is empty")
    if len(rows) > MAX_ROWS + HEADER_SCAN_ROWS:
        raise ImportFileError(f"More than {MAX_ROWS} rows; split the file and import it in parts")
    return rows


def detect_header(rows: list[list[str]]) -> tuple[int, dict[int, str]]:
    """Header row index and {column index: field}. Title rows above the header are skipped."""
    best_row, best_map = 0, {}
    for i, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        mapping, used = {}, set()
        for col, text in enumerate(row):
            f = _ALIAS_TO_FIELD.get(_key(text))
            if f and f not in used:
                mapping[col] = f
                used.add(f)
        if len(mapping) > len(best_map):
            best_row, best_map = i, mapping
    return best_row, best_map


def normalize_phone(raw: str) -> str | None:
    """+<digits>, or None when the number cannot be trusted."""
    p = re.sub(r"[\s\-().]", "", raw or "")
    if not p:
        return None
    explicit = p.startswith("+") or p.startswith("00")
    digits = re.sub(r"\D", "", p)
    if p.startswith("00"):
        digits = digits[2:]
    if not digits:
        return None
    if explicit:
        return f"+{digits}" if 8 <= len(digits) <= 15 else None
    if len(digits) == 10:
        return f"+91{digits}"
    if len(digits) == 11 and digits.startswith("0"):
        return f"+91{digits[1:]}"
    if len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"
    return None


_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d %b %Y")


def parse_date(raw: str) -> datetime.datetime | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


@dataclass
class RowResult:
    row: int  # 1-based sheet row number, so users can find it in their file
    status: str = "ready"
    issues: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    raw: list[str] = field(default_factory=list)
    existing_id: int | None = None

    def warn(self, message: str):
        self.issues.append(message)
        if self.status == "ready":
            self.status = "warning"

    def fail(self, message: str):
        self.issues.append(message)
        self.status = "error"


@dataclass
class Options:
    default_type: str = "consumer"
    default_source_id: int | None = None
    duplicates: str = "skip"  # "skip" | "update_blank"


def analyze(db: Session, rows: list[list[str]], header_row: int, mapping: dict[int, str], options: Options) -> list[RowResult]:
    sources = {}
    for s in db.query(LeadSource).filter(LeadSource.is_active == 1):
        sources[_key(s.name)] = s.id
        sources[_key(s.slug)] = s.id
    existing = {}
    for cid, phone in db.query(Contact.id, Contact.phone).filter(Contact.deleted_at.is_(None)):
        existing.setdefault(phone[-10:], cid)

    results, seen = [], {}
    for index in range(header_row + 1, len(rows)):
        raw = rows[index]
        r = RowResult(row=index + 1, raw=raw)
        d = {f: (raw[col].strip() if col < len(raw) else "") for col, f in mapping.items()}
        out = r.data

        out["name"] = d.get("name", "")
        if not out["name"]:
            r.fail("Name is missing")
        out["name"] = out["name"][:150]

        phone = normalize_phone(d.get("phone", ""))
        if phone is None:
            r.fail("Phone is missing" if not d.get("phone") else f"Phone '{d['phone']}' is not a valid number")
        else:
            out["phone"] = phone
            key = phone[-10:]
            if key in seen:
                r.fail(f"Same phone as row {seen[key]} in this file")
            else:
                seen[key] = r.row
                if key in existing and r.status != "error":
                    r.existing_id = existing[key]
                    r.status = "duplicate"
                    r.issues.append("Phone already in the CRM")

        alt = d.get("phone_alt", "")
        if alt:
            out["phone_alt"] = normalize_phone(alt)
            if out["phone_alt"] is None:
                r.warn("Alternate phone is not valid and was left out")

        out["district"] = (d.get("district") or "")[:80]
        if not out["district"]:
            out["district"] = MISSING_DISTRICT
            r.warn("District is missing; contact will be unverified")
        out["address"] = (d.get("address") or None) and d["address"][:255]
        out["notes"] = d.get("notes") or None

        ctype = _key(d.get("contact_type", ""))
        if not ctype:
            out["contact_type"] = options.default_type
        elif ctype in ("consumer", "partner", "dealer"):
            out["contact_type"] = ctype
        else:
            out["contact_type"] = options.default_type
            r.warn(f"Unknown type '{d['contact_type']}'; used {options.default_type}")

        src = d.get("lead_source", "")
        if src and _key(src) in sources:
            out["lead_source_id"] = sources[_key(src)]
        else:
            out["lead_source_id"] = options.default_source_id
            if src:
                r.warn(f"Unknown lead source '{src}'")

        if out["contact_type"] == "consumer":
            out["house_stage"] = (d.get("house_stage") or None) and d["house_stage"][:50]
        elif out["contact_type"] == "partner":
            out["work_area"] = (d.get("work_area") or None) and d["work_area"][:120]
            prof = d.get("profession_type", "")
            out["profession_type"] = ProfessionType.normalise(prof).value if prof else "other"
            if not prof:
                r.warn("Profession is missing; used 'other'")

        if d.get("created_at"):
            out["created_at"] = parse_date(d["created_at"])
            if out["created_at"] is None:
                r.warn(f"Date '{d['created_at']}' not understood; import time used")
        results.append(r)
    return results


def summarize(results: list[RowResult]) -> dict:
    counts = {"ready": 0, "warning": 0, "duplicate": 0, "error": 0}
    for r in results:
        counts[r.status] += 1
    return {"total": len(results), **counts}


def run_import(db: Session, results: list[RowResult], options: Options, assignee_ids: list[int], created_by: int) -> dict:
    """Write ready/warning rows (and fill blanks on duplicates when asked). One commit."""
    created = updated = 0
    for r in results:
        if r.status in ("ready", "warning"):
            d = dict(r.data)
            house_stage = d.pop("house_stage", None)
            work_area = d.pop("work_area", None)
            profession = d.pop("profession_type", None)
            if d.get("created_at") is None:
                d.pop("created_at", None)
            contact = Contact(**d, assigned_to=random.choice(assignee_ids), created_by=created_by)
            if d["contact_type"] == "consumer":
                contact.consumer_profile = ConsumerProfile(house_stage=house_stage)
            elif d["contact_type"] == "partner":
                contact.partner_profile = PartnerProfile(profession_type=profession, work_area=work_area)
            contact.lead_stages.append(LeadStage(stage="new_lead", changed_by=created_by))
            db.add(contact)
            created += 1
        elif r.status == "duplicate" and options.duplicates == "update_blank":
            contact = db.get(Contact, r.existing_id)
            changed = False
            for col in ("phone_alt", "address", "notes", "lead_source_id"):
                if getattr(contact, col) in (None, "") and r.data.get(col):
                    setattr(contact, col, r.data[col])
                    changed = True
            if contact.district in ("", MISSING_DISTRICT) and r.data.get("district") not in (None, "", MISSING_DISTRICT):
                contact.district = r.data["district"]
                changed = True
            updated += changed
    db.commit()
    skipped = sum(1 for r in results if r.status == "error" or (r.status == "duplicate" and options.duplicates == "skip"))
    return {"created": created, "updated": updated, "skipped": skipped}


def template_csv() -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(TEMPLATE_HEADERS)
    writer.writerow(TEMPLATE_EXAMPLE)
    return ("﻿" + out.getvalue()).encode("utf-8")
