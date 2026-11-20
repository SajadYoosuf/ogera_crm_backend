"""Contact import: read a CSV/XLSX sheet whose layout may be messy, validate every row and report.

`analyze` never writes. `run_import` works on the same analysis, so the preview and the result
cannot disagree. A row is `ready`, `warning` (imported with a fix-up), `duplicate` (already in
the CRM) or `error` (skipped, with a reason).

HOW PROFILE FIELDS WORK (important — read this):
    Consumer and partner profiles store their data in a JSONB column called `data`.
    What fields exist (e.g. "house_stage", "tank_type", "partner_type") is defined per-tenant
    in the `contact_field_definitions` table, NOT hardcoded here.

    At import time we:
      1. Load all active ContactFieldDefinition rows from the DB.
      2. Build column-detection aliases from each definition's field_name and field_label.
      3. Auto-detect which sheet columns map to which profile fields.
      4. Validate each raw cell value based on field_type (dropdown / text / number / boolean).
      5. Store the validated values in row_result.data["profile_data"] as a plain dict.
      6. run_import() passes that dict straight to ConsumerProfile(data=...) or PartnerProfile(data=...).

    Adding or renaming a field in contact_field_definitions requires ZERO code changes here.
"""

import csv
import datetime
import io
import random
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from models import ConsumerProfile, Contact, FollowupSchedule, LeadSource, LeadStage, PartnerProfile, PipelineStage
from models.contact_field_definition import ContactFieldDefinition
from phone_utils import phone_key
from business_time import ist_now

MAX_ROWS = 5000
MAX_BYTES = 5 * 1024 * 1024
HEADER_SCAN_ROWS = 10
MISSING_DISTRICT = None  # district is optional at creation; required for verification

# ── Core contact-table columns (same for every tenant) ────────────────────────
# Profile fields (house_stage, tank_type, partner_type, work_area, …) are NOT
# listed here — they are defined per-tenant in contact_field_definitions.

FIELDS = {
    "name": "Name",
    "phone": "Phone",
    "phone_alt": "Alternate phone",
    "district": "District",
    "address": "Address",
    "contact_type": "Type",
    "lead_source": "Lead source",
    "notes": "Notes",
    "created_at": "Created date",
}

# Column header aliases for core fields only.
# Profile field aliases are built at runtime from contact_field_definitions.
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
    "notes": ["notes", "note", "remarks", "remark", "comments", "comment", "description"],
    "created_at": ["createdat", "createddate", "date", "created", "enquirydate", "saledate"],
}
_ALIAS_TO_FIELD = {alias: f for f, aliases in ALIASES.items() for alias in aliases}

# Template headers — core fields only.
# Profile columns (house_stage, partner_type, etc.) are tenant-specific;
# call template_csv(field_defs) with the loaded defs to include them.
_CORE_TEMPLATE_HEADERS = ["Name", "Phone", "Alternate phone", "District", "Address",
                           "Type", "Lead source", "Notes", "Created date"]
_CORE_TEMPLATE_EXAMPLE = ["Asha Nair", "9876543210", "", "Kozhikode", "12 MG Road",
                           "consumer", "Instagram", "Wants a 1000 L tank", "2026-10-01"]


# ── Errors ────────────────────────────────────────────────────────────────────

class ImportFileError(ValueError):
    """The file as a whole cannot be read (wrong type, too big, empty)."""


# ── Cell helpers ──────────────────────────────────────────────────────────────

def _key(text: str) -> str:
    """Normalise a header string to letters+digits only for fuzzy matching."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _option_key(text: str) -> str:
    """Normalise a dropdown option for matching: 'Under Construction' -> 'under_construction'."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


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


# ── File reading ──────────────────────────────────────────────────────────────

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
        except Exception as exc:
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


# ── Header detection ──────────────────────────────────────────────────────────

def detect_header(rows: list[list[str]]) -> tuple[int, dict[int, str]]:
    """
    Detect the header row using core-field aliases only.
    Returns (header_row_index, {col_index: field_name}).

    Call detect_all_columns() instead when you have DB access — it also maps
    profile field columns from contact_field_definitions.
    """
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


# ── Dynamic profile-field helpers ─────────────────────────────────────────────

def load_field_defs(db: Session) -> dict[str, list[ContactFieldDefinition]]:
    """
    Load all active ContactFieldDefinition rows, grouped by entity_type.

    Returns {"consumer": [...], "partner": [...]} where each list is sorted
    by sort_order.  Called once per import request and passed into the other
    profile helpers so we don't hit the DB for every row.
    """
    rows = (
        db.query(ContactFieldDefinition)
        .filter_by(is_active=True)
        .order_by(ContactFieldDefinition.sort_order)
        .all()
    )
    result: dict[str, list[ContactFieldDefinition]] = {"consumer": [], "partner": []}
    for defn in rows:
        if defn.entity_type in result:
            result[defn.entity_type].append(defn)
    return result


def _profile_col_aliases(field_defs: dict[str, list[ContactFieldDefinition]]) -> dict[str, tuple[str, str]]:
    """
    Build a column-detection map from field definitions.

    Returns {normalised_header_text: (entity_type, field_name)} so the header
    scanner can recognise profile columns from any tenant's field definitions.

    Example: if the tenant defines field_name="house_stage", field_label="House Stage":
        {"housestage": ("consumer", "house_stage"),
         "house_stage": ("consumer", "house_stage")}
    """
    out: dict[str, tuple[str, str]] = {}
    for entity_type, defs in field_defs.items():
        for defn in defs:
            # field_name as a key (e.g. "house_stage" -> "housestage")
            out[_key(defn.field_name)] = (entity_type, defn.field_name)
            # field_label as a key (e.g. "House Stage" -> "housestage")
            if defn.field_label:
                out[_key(defn.field_label)] = (entity_type, defn.field_name)
    return out


def detect_all_columns(
    rows: list[list[str]],
    field_defs: dict[str, list[ContactFieldDefinition]],
) -> tuple[int, dict[int, str]]:
    """
    Like detect_header() but also maps profile field columns.

    After finding the header row with core-field aliases, scans any unmapped
    columns against field definitions from the DB, adding them to the mapping.
    Use this in the import route so profile fields are included in the mapping
    returned to the frontend.

    Returns (header_row_index, {col_index: field_name}).
    Profile field columns use their field_name directly (e.g. "house_stage").
    """
    header_row, col_map = detect_header(rows)
    if header_row >= len(rows):
        return header_row, col_map

    profile_aliases = _profile_col_aliases(field_defs)
    for col, text in enumerate(rows[header_row]):
        if col not in col_map:
            key = _key(text)
            if key in profile_aliases:
                _entity_type, field_name = profile_aliases[key]
                col_map[col] = field_name
    return header_row, col_map


def _validate_profile_field(
    raw: str,
    defn: ContactFieldDefinition,
    r: "RowResult",
) -> object:
    """
    Coerce and validate a raw cell value for a single profile field.

    - dropdown : must match one of defn.options (normalised comparison)
    - number   : parsed to int or float
    - boolean  : yes/no/true/false/1/0
    - text     : trimmed string, truncated to 500 chars

    Returns the coerced value, or None when the value is absent or invalid.
    A warning is added to `r` when the raw value exists but is invalid.
    """
    label = defn.field_label or defn.field_name

    if not raw or not raw.strip():
        return None

    if defn.field_type == "dropdown":
        opts: list[str] = defn.options or []
        # Build {normalised_key: stored_value}
        opt_map: dict[str, str] = {}
        for opt in opts:
            opt_map[_option_key(opt)] = opt
            # Also map a human-readable label derived from the slug
            # e.g. "under_construction" -> "Under Construction" -> "under_construction"
            opt_map[_option_key(opt.replace("_", " ").title())] = opt
        key = _option_key(raw.strip())
        if key in opt_map:
            return opt_map[key]
        r.warn(f"{label}: '{raw}' is not a valid option; left blank. Valid: {', '.join(opts)}")
        return None

    elif defn.field_type == "number":
        try:
            val = float(raw.strip())
            return int(val) if val == int(val) else val
        except (ValueError, AttributeError):
            r.warn(f"{label}: '{raw}' is not a valid number; left blank")
            return None

    elif defn.field_type == "boolean":
        lower = raw.strip().lower()
        if lower in ("1", "yes", "true", "y"):
            return True
        if lower in ("0", "no", "false", "n"):
            return False
        r.warn(f"{label}: '{raw}' should be yes/no; left blank")
        return None

    else:  # text
        return raw.strip()[:500] or None


# ── Phone helpers ─────────────────────────────────────────────────────────────

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


# ── Date helpers ──────────────────────────────────────────────────────────────

_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y",
                 "%d.%m.%Y", "%d/%m/%y", "%d %b %Y")


def parse_date(raw: str) -> datetime.datetime | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


# ── Row result ────────────────────────────────────────────────────────────────

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


# ── Core analysis ─────────────────────────────────────────────────────────────

def analyze(
    db: Session,
    rows: list[list[str]],
    header_row: int,
    mapping: dict[int, str],
    options: Options,
    field_defs: dict[str, list[ContactFieldDefinition]] | None = None,
) -> list[RowResult]:
    """
    Validate every data row and return a RowResult for each.

    HOW THIS FUNCTION WORKS:
        1. Loads lead sources and existing phones from the DB for validation.
        2. For each data row (after the header), maps raw cells to field names
           using `mapping` (column index -> field name).
        3. Validates and normalises core contact fields (name, phone, etc.).
        4. For profile fields (anything not in FIELDS), looks up the matching
           ContactFieldDefinition and validates the value generically by field_type.
        5. Stores validated profile values in out["profile_data"] as a dict.
           run_import() passes this dict directly to ConsumerProfile(data=...) or
           PartnerProfile(data=...) without any field-specific code.

    WHY field_defs IS OPTIONAL:
        When the caller already loaded field_defs (e.g. the import route), pass it
        in to avoid a second DB query.  When None, we load it ourselves.

    NEVER WRITES to the DB — this is a pure validation/analysis function.
    """
    if field_defs is None:
        field_defs = load_field_defs(db)

    # Build {field_name: ContactFieldDefinition} for quick lookup
    all_field_def_map: dict[str, ContactFieldDefinition] = {}
    for defs in field_defs.values():
        for defn in defs:
            all_field_def_map[defn.field_name] = defn

    # Core field names — anything else in the mapping is a profile field
    core_fields = set(FIELDS.keys())

    # Lead source lookup: {normalised_name_or_slug: id}
    sources: dict[str, int] = {}
    for s in db.query(LeadSource).filter(LeadSource.is_active == 1):
        sources[_key(s.name)] = s.id
        sources[_key(s.slug)] = s.id

    # Existing contact phones for duplicate detection: {last_10_digits: contact_id}
    existing: dict[str, int] = {}
    for cid, phone in db.query(Contact.id, Contact.phone).filter(Contact.deleted_at.is_(None)):
        if phone:
            existing.setdefault(phone[-10:], cid)

    results: list[RowResult] = []
    seen: dict[str, int] = {}  # {last_10_digits: row_number} — dedup within the file

    for index in range(header_row + 1, len(rows)):
        raw = rows[index]
        r = RowResult(row=index + 1, raw=raw)
        d = {f: (raw[col].strip() if col < len(raw) else "") for col, f in mapping.items()}
        out = r.data

        # ── Name ──────────────────────────────────────────────────────────────
        out["name"] = (d.get("name") or "")[:150] or None
        if not out["name"]:
            r.warn("Name is missing; contact will be unverified")

        # ── Phone ─────────────────────────────────────────────────────────────
        phone = normalize_phone(d.get("phone", ""))
        if phone is None:
            r.fail("Phone is missing" if not d.get("phone") else f"Phone '{d['phone']}' is not a valid number")
        else:
            out["phone"] = phone
            tail = phone[-10:]
            if tail in seen:
                r.fail(f"Same phone as row {seen[tail]} in this file")
            else:
                seen[tail] = r.row
                if tail in existing and r.status != "error":
                    r.existing_id = existing[tail]
                    r.status = "duplicate"
                    r.issues.append("Phone already in the CRM")

        # ── Alternate phone ───────────────────────────────────────────────────
        alt = d.get("phone_alt", "")
        if alt:
            out["phone_alt"] = normalize_phone(alt)
            if out["phone_alt"] is None:
                r.warn("Alternate phone is not valid and was left out")

        # ── Location fields ───────────────────────────────────────────────────
        out["district"] = (d.get("district") or None) and d["district"][:80]
        if not out["district"]:
            out["district"] = MISSING_DISTRICT
            r.warn("District is missing; contact will be unverified")
        out["address"] = (d.get("address") or None) and d["address"][:255]
        out["notes"] = d.get("notes") or None

        # ── Contact type ──────────────────────────────────────────────────────
        ctype = _key(d.get("contact_type", ""))
        if not ctype:
            out["contact_type"] = options.default_type
        elif ctype in ("consumer", "partner"):
            out["contact_type"] = ctype
        else:
            out["contact_type"] = options.default_type
            r.warn(f"Unknown type '{d['contact_type']}'; used {options.default_type}")

        # ── Lead source ───────────────────────────────────────────────────────
        src = d.get("lead_source", "")
        if src and _key(src) in sources:
            out["lead_source_id"] = sources[_key(src)]
        else:
            out["lead_source_id"] = options.default_source_id
            if src:
                r.warn(f"Unknown lead source '{src}'")

        # ── Profile fields (fully dynamic from contact_field_definitions) ─────
        # Build the profile data dict from whatever profile columns are in the mapping.
        # We only apply fields that belong to the row's contact_type.
        entity_type = out["contact_type"]  # "consumer" or "partner"
        profile_data: dict[str, object] = {}

        for field_name, raw_value in d.items():
            if field_name in core_fields:
                continue  # handled above
            defn = all_field_def_map.get(field_name)
            if defn is None:
                continue  # unknown field — skip silently (may be from wrong entity_type's column)
            if defn.entity_type != entity_type:
                continue  # column belongs to the other type — not relevant for this row
            value = _validate_profile_field(raw_value, defn, r)
            if value is not None:
                profile_data[field_name] = value

        out["profile_data"] = profile_data

        # ── Created date ──────────────────────────────────────────────────────
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


# ── Write ─────────────────────────────────────────────────────────────────────

def _new_lead_stage_id(db: Session, contact_type: str) -> int:
    """Return the PipelineStage.id for the 'new_lead' stage of the given contact_type."""
    st = db.query(PipelineStage).filter(
        PipelineStage.slug == "new_lead",
        PipelineStage.contact_type == contact_type,
        PipelineStage.is_active == 1,
    ).first()
    if not st:
        raise RuntimeError(f"Pipeline not seeded: new_lead stage missing for {contact_type}")
    return st.id


def run_import(
    db: Session,
    results: list[RowResult],
    options: Options,
    assignee_ids: list[int],
    created_by: int,
) -> dict:
    """
    Write ready/warning rows to the database (and fill blanks on duplicates when asked).
    All rows are committed in a single transaction.

    HOW PROFILE DATA IS SAVED:
        Each RowResult.data["profile_data"] is a plain dict built by analyze().
        It is passed directly to ConsumerProfile(data=...) or PartnerProfile(data=...).
        No field-specific code here — the dict already contains only valid values.

    INPUTS:
        results      — from analyze(); rows with status "error" are skipped
        options      — duplicates policy
        assignee_ids — round-robined for the contact's assigned_to
        created_by   — employee id of the user who triggered the import
    """
    created = updated = 0
    # Cache new_lead stage ids per type to avoid one query per row
    new_lead_ids: dict[str, int] = {}

    for r in results:
        if r.status in ("ready", "warning"):
            d = dict(r.data)
            profile_data: dict = d.pop("profile_data", {})
            if d.get("created_at") is None:
                d.pop("created_at", None)

            ct = d["contact_type"]
            if ct not in new_lead_ids:
                new_lead_ids[ct] = _new_lead_stage_id(db, ct)

            contact = Contact(
                **d,
                phone_key=phone_key(d["phone"]),
                assigned_to=random.choice(assignee_ids),
                created_by=created_by,
            )

            # Attach profile — data dict passed straight through, no field-specific logic
            if ct == "consumer":
                contact.consumer_profile = ConsumerProfile(data=profile_data)
            elif ct == "partner":
                contact.partner_profile = PartnerProfile(data=profile_data)

            contact.lead_stages.append(LeadStage(stage_id=new_lead_ids[ct], changed_by=created_by))
            contact.followup_schedules = [FollowupSchedule(
                assigned_to=contact.assigned_to,
                due_at=ist_now() + datetime.timedelta(hours=24),
                status="pending",
                channel="call",
                notes="Initial contact regarding new lead",
            )]
            db.add(contact)
            created += 1

        elif r.status == "duplicate" and options.duplicates == "update_blank":
            contact = db.get(Contact, r.existing_id)
            changed = False
            for col in ("name", "phone_alt", "address", "notes", "lead_source_id"):
                if getattr(contact, col) in (None, "") and r.data.get(col):
                    setattr(contact, col, r.data[col])
                    changed = True
            if not contact.district and r.data.get("district"):
                contact.district = r.data["district"]
                changed = True
            updated += changed

    db.commit()
    skipped = sum(
        1 for r in results
        if r.status == "error" or (r.status == "duplicate" and options.duplicates == "skip")
    )
    return {"created": created, "updated": updated, "skipped": skipped}


# ── Template CSV ──────────────────────────────────────────────────────────────

def template_csv(field_defs: dict[str, list[ContactFieldDefinition]] | None = None) -> bytes:
    """
    Download template CSV for the import form.

    When field_defs is passed (from the import route with DB access), profile
    field columns are appended after the core columns so the user knows exactly
    what their tenant's profile fields are called.
    """
    headers = list(_CORE_TEMPLATE_HEADERS)
    example = list(_CORE_TEMPLATE_EXAMPLE)

    if field_defs:
        for entity_type in ("consumer", "partner"):
            for defn in field_defs.get(entity_type, []):
                col_label = defn.field_label or defn.field_name
                headers.append(col_label)
                # Put a sample value so the user can see the format
                if defn.field_type == "dropdown" and defn.options:
                    example.append(defn.options[0])
                elif defn.field_type == "number":
                    example.append("0")
                elif defn.field_type == "boolean":
                    example.append("yes")
                else:
                    example.append("")

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(headers)
    writer.writerow(example)
    # UTF-8 BOM so Excel opens it without mangling characters
    return ("﻿" + out.getvalue()).encode("utf-8")
