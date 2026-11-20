"""
Lead import — turns a messy, raw lead export (Meta Ads, JustDial, a website
form dump, or any other spreadsheet whose columns we don't control in advance)
into Lead rows.

WHY THIS FILE REUSES contact_import.py:
    Reading .csv/.xlsx files, sniffing the header row, and normalising a phone
    number are not contact-specific problems — contact_import.py already solved
    them. This file imports those generic pieces (read_rows, ImportFileError,
    normalize_phone, _key) instead of re-solving them. Only the field list
    (FIELDS/ALIASES) and the row → Lead mapping are Leads-specific.

WHAT'S DIFFERENT FROM A CONTACT IMPORT (on purpose — Leads needs much less):
    - phone is the only required field. name, address, notes and tags are all
      optional — a lead with just a phone number is still useful.
    - No email column at all (manual lead entry already dropped email).
    - No per-tenant "profile fields" — Leads doesn't have Contacts' dynamic
      consumer/partner fields, so there is nothing to look up from the DB there.
    - Duplicates are always skipped (never merged into the existing row) —
      simpler than Contacts' "update_blank" option, which this MVP leaves out.
    - Imported leads never trigger the automatic WhatsApp welcome message —
      that message is for a lead someone just created live, not a batch of
      possibly old rows from an ad platform's export.

FUNCTIONS IN THIS FILE:
    detect_header(rows)          — finds the header row using Lead column aliases
    analyze(db, ...)              — validates every row; never writes to the DB
    summarize(results)            — counts by status for the preview screen
    run_import(db, ...)           — writes ready/warning rows as Lead rows
    template_csv()                 — blank CSV with the recognised headers
"""

import csv
import datetime
import io
import re
from dataclasses import dataclass, field as dc_field

from sqlalchemy.orm import Session

from contact_import import ImportFileError, read_rows, normalize_phone, parse_date, _key  # noqa: F401 (re-exported for routes/leads.py)
from models.contact import Contact
from models.employee import Employee
from models.lead import Lead
from models.lead_source import LeadSource
from phone_utils import phone_key as compute_phone_key
from services.lead_service import auto_assign, compute_score

HEADER_SCAN_ROWS = 10

# ── Lead fields this import understands ───────────────────────────────────────

FIELDS = {
    "first_name": "First name",
    "last_name": "Last name",
    "phone": "Phone",
    "address": "Address",
    "notes": "Notes / Description",
    "tags": "Tags",
    "lead_source": "Source",
    "created_at": "Created date",
}

ALIASES = {
    "first_name": ["firstname", "name", "fullname", "customername", "contactname", "leadname", "clientname"],
    "last_name": ["lastname", "surname"],
    "phone": ["phone", "phoneno", "phonenumber", "mobile", "mobileno", "mobilenumber", "contact",
              "contactno", "contactnumber", "whatsapp", "whatsappnumber", "cell", "tel", "telephone"],
    "address": ["address", "fulladdress", "addr", "streetaddress", "location", "district", "city"],
    "notes": ["notes", "note", "remarks", "remark", "comments", "comment", "description", "message", "enquiry", "query"],
    "tags": ["tags", "tag", "labels", "label"],
    "lead_source": ["source", "leadsource", "sourceofsale", "origin", "channel", "campaign", "adname", "platform"],
    "created_at": ["createdtime", "createdat", "createddate", "created", "date", "leaddate", "enquirydate", "timestamp"],
}
_ALIAS_TO_FIELD = {alias: f for f, aliases in ALIASES.items() for alias in aliases}

_TEMPLATE_HEADERS = ["First name", "Last name", "Phone", "Address", "Notes / Description", "Tags", "Source", "Created date"]
_TEMPLATE_EXAMPLE = ["Asha", "Nair", "9876543210", "12 MG Road, Kozhikode", "Interested in a 1000L tank", "hot, calicut", "Meta Ads", "2026-08-19"]


def _strip_label(raw: str) -> str:
    """Meta exports prefix phone numbers with a field tag, e.g. "p:+97477869005"."""
    return re.sub(r"^\s*[A-Za-z]{1,3}:\s*", "", raw or "")


def _phones_in(raw: str) -> list[str]:
    """
    Every valid number in a phone cell, in order. A cell can hold several
    numbers separated by spaces or commas, e.g. "Mob: +971544921917 9656272908".
    """
    tokens = re.findall(r"\+?\d[\d\-().]*\d", _strip_label(raw))
    found = []
    for token in tokens:
        phone = normalize_phone(token)
        if phone is None and token[:1] != "+" and 11 <= len(re.sub(r"\D", "", token)) <= 15:
            phone = "+" + re.sub(r"\D", "", token)
        if phone and phone not in found:
            found.append(phone)
    return found


def _lead_date(raw: str) -> datetime.datetime | None:
    """
    The calendar date of a date cell, as midnight.

    ISO timestamps (Meta Ads: "2026-08-19T06:58:29-05:00") are cut at the date
    part, so the timezone never shifts the day. Anything else goes through the
    same parser the Contacts import uses.
    """
    iso = re.match(r"\s*(\d{4}-\d{2}-\d{2})", raw)
    if iso:
        return datetime.datetime.strptime(iso.group(1), "%Y-%m-%d")
    text = raw.strip()
    return parse_date(text) or parse_date(text.split()[0]) if text else None


def detect_header(rows: list[list[str]]) -> tuple[int, dict[int, str]]:
    """Finds the header row using the Lead ALIASES above. Returns (row_index, {col: field})."""
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


# ── Row result ────────────────────────────────────────────────────────────────

@dataclass
class RowResult:
    row: int  # 1-based sheet row number, so staff can find it in their file
    status: str = "ready"  # ready | warning | duplicate | error
    issues: list[str] = dc_field(default_factory=list)
    data: dict = dc_field(default_factory=dict)
    raw: list[str] = dc_field(default_factory=list)
    existing_id: int | None = None
    existing_type: str | None = None  # "lead" | "contact"

    def warn(self, message: str):
        self.issues.append(message)
        if self.status == "ready":
            self.status = "warning"

    def fail(self, message: str):
        self.issues.append(message)
        self.status = "error"


@dataclass
class Options:
    # Fallback source_id when a row's source column is blank or doesn't match
    # an active lead_sources row (per-row detection is the default behaviour).
    default_source_id: int | None = None


# ── Core analysis ─────────────────────────────────────────────────────────────

def analyze(db: Session, rows: list[list[str]], header_row: int, mapping: dict[int, str], options: Options) -> list[RowResult]:
    """
    Validate every data row and return a RowResult for each. Never writes to the DB.

    Phone is the only hard requirement — a row with no valid phone is an error
    and will not be imported. Everything else is optional and only produces a
    warning (so the row still imports, just with a gap staff can fill in later).
    """
    sources: dict[str, int] = {}
    for s in db.query(LeadSource).filter(LeadSource.is_active == 1):
        for label in (s.name, s.slug):
            if label:
                sources[_key(label)] = s.id

    # Existing phones across BOTH leads and contacts (last 10 digits), so an
    # imported row that already exists either way is flagged as a duplicate.
    existing: dict[str, tuple[str, int, str]] = {}
    for lid, phone, fname in db.query(Lead.id, Lead.phone, Lead.first_name).filter(Lead.deleted_at.is_(None)):
        if phone:
            existing.setdefault(phone[-10:], ("lead", lid, fname or ""))
    for cid, phone, cname in db.query(Contact.id, Contact.phone, Contact.name).filter(Contact.deleted_at.is_(None)):
        if phone:
            existing.setdefault(phone[-10:], ("contact", cid, cname or ""))

    results: list[RowResult] = []
    seen: dict[str, int] = {}  # last-10-digits -> row number, for in-file duplicates

    for index in range(header_row + 1, len(rows)):
        raw = rows[index]
        if not any(cell.strip() for cell in raw):
            continue  # fully blank row (common at the end of an exported sheet) — not a real row
        r = RowResult(row=index + 1, raw=raw)
        d = {f: (raw[col].strip() if col < len(raw) else "") for col, f in mapping.items()}
        out = r.data

        # ── Name — optional ───────────────────────────────────────────────────
        out["first_name"] = (d.get("first_name") or "")[:200] or None
        out["last_name"] = (d.get("last_name") or "")[:100] or None
        if not out["first_name"]:
            r.warn("Name is missing")

        # ── Phone — the only required field ───────────────────────────────────
        phones = _phones_in(d.get("phone", ""))
        phone = phones[0] if phones else None
        extra_phones = phones[1:]
        if phone is None:
            r.fail("Phone is missing" if not d.get("phone") else f"Phone '{d['phone']}' is not a valid number")
        else:
            out["phone"] = phone
            out["extra_phones"] = extra_phones
            tail = phone[-10:]
            if tail in seen:
                r.fail(f"Same phone as row {seen[tail]} in this file")
            else:
                seen[tail] = r.row
                if tail in existing and r.status != "error":
                    kind, existing_id, existing_name = existing[tail]
                    r.existing_id = existing_id
                    r.existing_type = kind
                    r.status = "duplicate"
                    r.issues.append(f"Phone already in the CRM (existing {kind}: {existing_name or 'unnamed'})")

        # ── Address / notes / tags — all optional ─────────────────────────────
        out["address"] = (d.get("address") or None) and d["address"][:500]
        extra = f"Other number: {', '.join(extra_phones)}" if extra_phones else None
        out["notes"] = " · ".join(filter(None, [d.get("notes") or None, extra])) or None
        raw_tags = d.get("tags", "")
        out["tags"] = [t.strip() for t in re.split(r"[,;]", raw_tags) if t.strip()] or None

        # ── Created date — Meta exports look like "2026-08-19T06:58:29-05:00".
        #    Only the calendar date is kept, exactly as written in the file, so the
        #    lead shows the day the enquiry came in, not the time of import.
        raw_date = d.get("created_at", "")
        if raw_date:
            out["created_at"] = _lead_date(raw_date)
            if out["created_at"] is None:
                r.warn(f"Date '{raw_date}' not understood; import date used")

        # ── Source — detected per row from the sheet, falling back to the
        #    import's default when the column is blank or doesn't match ───────
        src = d.get("lead_source", "")
        if src and _key(src) in sources:
            out["source_id"] = sources[_key(src)]
        else:
            out["source_id"] = options.default_source_id
            if src:
                r.warn(f"Unknown source '{src}'; used default")

        results.append(r)
    return results


def summarize(results: list[RowResult]) -> dict:
    counts = {"ready": 0, "warning": 0, "duplicate": 0, "error": 0}
    for r in results:
        counts[r.status] += 1
    return {"total": len(results), **counts}


# ── Write ─────────────────────────────────────────────────────────────────────

def run_import(
    db: Session,
    results: list[RowResult],
    created_by: int,
    assign_mode: str,  # "auto" | "unassigned" | "specific"
    specific_employee_id: int | None = None,
) -> dict:
    """
    Write ready/warning rows as Lead rows. Duplicate and error rows are skipped.
    All rows are committed in a single transaction.

    assign_mode:
        "auto"       — each row gets the same least-loaded-employee pick manual
                        creation uses (services.lead_service.auto_assign)
        "unassigned" — assigned_to stays NULL; a manager sorts them out later
        "specific"   — every row goes to specific_employee_id

    branch_id defaults to the importing employee's own branch, same rule as
    manual creation — an import is something an employee does, so it behaves
    like one (unlike the webhook/inbound channels, which have no employee).
    """
    creator = db.get(Employee, created_by)
    branch_id = creator.branch_id if creator else None

    created = 0
    for r in results:
        if r.status not in ("ready", "warning"):
            continue
        d = r.data

        if assign_mode == "specific":
            assigned_to = specific_employee_id
        elif assign_mode == "auto":
            assigned_to = auto_assign(db)
        else:
            assigned_to = None

        lead = Lead(
            # The live leads.first_name column is NOT NULL, so a missing name is saved as "".
            first_name=d["first_name"] or "",
            last_name=d["last_name"],
            phone=d["phone"],
            phone_key=compute_phone_key(d["phone"]),
            source_id=d["source_id"],
            address=d["address"],
            notes=d["notes"],
            tags=d["tags"],
            status="new",
            assigned_to=assigned_to,
            created_by=created_by,
            branch_id=branch_id,
            score=0,
            # Only when the file had a readable date; otherwise the column default (import time) applies.
            **({"created_at": d["created_at"]} if d.get("created_at") else {}),
        )
        lead.score = compute_score(lead)
        db.add(lead)
        created += 1

    db.commit()
    skipped = sum(1 for r in results if r.status in ("error", "duplicate"))
    return {"created": created, "skipped": skipped}


# ── Template CSV ──────────────────────────────────────────────────────────────

def template_csv() -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(_TEMPLATE_HEADERS)
    writer.writerow(_TEMPLATE_EXAMPLE)
    return ("﻿" + out.getvalue()).encode("utf-8")  # BOM keeps Excel from mangling non-ASCII names
