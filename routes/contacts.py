"""
Contact routes — all HTTP endpoints for creating, reading, updating and deleting contacts.

WHAT IS THIS FILE?
------------------
This is a FastAPI "router" module. It defines every HTTP endpoint that starts with
`/contacts`. FastAPI reads the decorators (`@router.get`, `@router.post`, etc.) and
registers them when the app starts.

HOW FASTAPI ROUTES WORK (simple version):
    @router.get("/{contact_id}")   →   GET /contacts/42
    def get_contact(contact_id: int, db = Depends(get_tenant_db)):
        ...                            FastAPI injects `db` automatically via Depends()

    The `Depends(get_current_user)` on the router itself means EVERY endpoint in this
    file requires a valid JWT token. Anonymous requests get a 401 automatically.

HELPER FUNCTIONS IN THIS FILE:
    _ensure_phone_free()    — raise 409 if the phone already belongs to another contact
    _ensure_referrer()      — validate that the referring contact exists and is not self
    _check_type_profiles()  — ensure the profile block matches the contact type
    _validate_*_profile_data() — validate profile JSONB data against contact_field_definitions

ROUTE OVERVIEW:
    GET    /contacts                  — paged list with filters
    GET    /contacts/export           — download CSV / XLSX / PDF
    GET    /contacts/import/template  — download blank import CSV
    POST   /contacts/import/analyze   — dry-run a CSV/XLSX file, returns row-level preview
    POST   /contacts/import/commit    — write validated rows to the database
    GET    /contacts/type-counts      — badge counts per type
    GET    /contacts/partner-types    — active partner profession options
    GET    /contacts/{id}             — single contact detail
    POST   /contacts                  — create a new contact
    PUT    /contacts/{id}             — update a contact
    DELETE /contacts/{id}             — soft-delete a contact
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, or_, select, update as sa_update
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import SQLAlchemyError, OperationalError
import contact_export
import contact_import
from business_time import ist_day_start_utc
from auth import get_current_user, get_tenant_db
from contact_lookup import get_live_contact
from phone_utils import phone_key
from request_context import client_platform
from services.lead_access import is_head
from verification import missing_fields, verified_clause
from models import Contact, ConsumerProfile, Employee, LeadSource, PartnerProfile, Role
from models.lead_stage import LeadStage
from models.pipeline_stage import PipelineStage
from models.record_log import RecordLog
from schemas import (
    ContactCreate,
    ContactListItem,
    ContactListResponse,
    ContactResponse,
    ContactStageResponse,
    ContactStageSet,
    ContactType,
    ContactUpdate,
    ContactBulkAssignRequest,
    ContactBulkAssignResponse,
    ContactStatsResponse,
)
from services.contact_field_service import validate_profile_data
from services.record_log_service import add_log

router = APIRouter(
    prefix="/contacts", tags=["contacts"], dependencies=[Depends(get_current_user)]
)

# Roles that are never auto-assigned.
# Admins and managers oversee leads; they don't handle follow-ups themselves.
NON_ASSIGNABLE_ROLES = ("admin", "manager")

logger = logging.getLogger(__name__)


def _run_notification(db: Session, send) -> None:
    """
    Run a notification write without letting it break the request.

    The write goes in a SAVEPOINT. If it fails, only the savepoint is undone, so the
    session stays usable for the response. The contact itself is already committed
    by the caller and is never rolled back here.
    """
    try:
        with db.begin_nested():
            send()
        db.commit()
    except Exception:
        logger.exception("Notification failed; the contact change was kept")
        db.rollback()


def _ensure_phone_free(db: Session, phone: str, user: Employee, exclude_id: int | None = None) -> None:
    """
    Guarantee that the phone number is not already used by another live contact.

    WHY phone_key AND NOT phone?
        Different callers format phone numbers differently ("+91 98765 43210" vs
        "9876543210"). We compare only the last 10 digits (phone_key) so that
        formatting differences never let a duplicate slip through.

    `exclude_id` is used on UPDATE so the contact doesn't conflict with itself.

    SQL equivalent:
        SELECT id FROM contacts
        WHERE phone_key = :key AND deleted_at IS NULL AND id != :exclude_id
        LIMIT 1;

    INPUT:  db session, the raw phone string, the current user, optional id to exclude
    RAISES: 409 with the conflicting contact's id if a duplicate is found
    """
    q = db.query(Contact.id).filter(Contact.phone_key == phone_key(phone), Contact.deleted_at.is_(None))
    if exclude_id is not None:
        q = q.filter(Contact.id != exclude_id)
    hit = q.first()
    if hit is not None:
        raise HTTPException(status_code=409, detail=f"A contact with this phone number already exists (contact {hit.id})")


def _ensure_referrer(db: Session, referred_by_id: int | None, contact_id: int | None = None):
    """
    Validate that the referring contact exists and is not the same contact.

    Called before create and update to prevent:
        1. Referencing a deleted or nonexistent contact.
        2. A contact referring itself (circular reference).

    INPUT:  db session, the referred_by_id from the request, optional current contact id
    RAISES: 400 on self-referral or unknown referrer
    """
    if referred_by_id is None:
        return
    if referred_by_id == contact_id:
        raise HTTPException(status_code=400, detail="A contact cannot refer itself")
    referrer = db.query(Contact).filter(Contact.id == referred_by_id).first()
    if not referrer or referrer.deleted_at:
        raise HTTPException(status_code=400, detail="Referring contact not found")


def _check_type_profiles(contact_type: str, consumer_profile, partner_profile) -> None:
    """
    Ensure the caller isn't sending a profile block for the wrong contact type.

    For example, sending `partner_profile` for a consumer contact is a mistake.
    This catches it early before any DB writes happen.

    INPUT:  the resolved contact_type string, and the two optional profile inputs
    RAISES: 400 if a profile block does not match the contact type
    """
    if consumer_profile is not None and contact_type != "consumer":
        raise HTTPException(status_code=400, detail="consumer_profile is only allowed for consumer contacts")
    if partner_profile is not None and contact_type != "partner":
        raise HTTPException(status_code=400, detail="partner_profile is only allowed for partner contacts")


def _validate_consumer_profile_data(db: Session, data: dict) -> None:
    """
    Validate consumer profile JSONB data against the tenant's field definitions.

    Reads active ContactFieldDefinition rows for entity_type='consumer' and checks
    required fields, dropdown values, and number types.

    INPUT:  db session, the incoming data dict (e.g. {"house_stage": "occupied"})
    RAISES: 422 on validation failure (required missing, bad dropdown value, non-numeric)
    """
    if not data:
        return
    validate_profile_data(db, "consumer", data)


def _validate_partner_profile_data(db: Session, data: dict) -> None:
    """
    Validate partner profile JSONB data against the tenant's field definitions.

    Reads active ContactFieldDefinition rows for entity_type='partner' and checks
    required fields, dropdown values, and number types.

    INPUT:  db session, the incoming data dict (e.g. {"partner_type": "plumber"})
    RAISES: 422 on validation failure
    """
    if not data:
        return
    validate_profile_data(db, "partner", data)


def contact_filters(
    contact_type: ContactType | None = None,
    partner_type: str | None = Query(None, description="Partner type key, e.g. plumber"),
    district: str | None = None,
    search: str | None = Query(None, description="Matches name or phone"),
    lead_source_id: int | None = None,
    assigned_to: int | None = None,
    created_by: int | None = None,
    branch_id: int | None = Query(None, description="Filter by branch id"),
    unattributed: bool | None = Query(None, description="true = only contacts with no recorded creator"),
    verified: bool | None = Query(None, description="true = only verified, false = only unverified"),
    created_from: date | None = Query(None, description="Created on or after this date"),
    created_to: date | None = Query(None, description="Created on or before this date"),
) -> dict:
    """
    Collect active query filters from URL parameters into a plain dict.

    This function is used as a FastAPI `Depends()` dependency. FastAPI automatically
    reads the parameters from the URL (e.g. `?contact_type=partner&district=Calicut`)
    and passes them to both the list endpoint and the export endpoint.

    WHY SHARE BETWEEN LIST AND EXPORT?
        If each endpoint declared its own filter parameters, they could get out of
        sync. By using the same `Depends(contact_filters)`, both endpoints always
        filter identically — what you see in the list is exactly what you download.

    OUTPUT: dict of only the filters that were provided (None values are excluded).
            e.g. {"contact_type": ContactType.partner, "district": "Calicut"}
    """
    params = dict(locals())
    return {k: v for k, v in params.items() if v is not None}


def contact_query(db: Session, f: dict, user: Employee):
    """
    Build a SQLAlchemy query for contacts matching the given filters.

    This is the core query used by the list endpoint, the export, and the count.
    It is kept in one place so all three always return the same data.

    SQL equivalent (simplified):
        SELECT contacts.* FROM contacts
        WHERE contacts.deleted_at IS NULL AND <all active filters>

    INPUT:
        db   — SQLAlchemy session
        f    — filter dict from contact_filters()
        user — the currently signed-in employee (ownership scope)

    OUTPUT: SQLAlchemy Query object (not yet executed; caller calls .all() or .count())
    """
    q = db.query(Contact).filter(Contact.deleted_at.is_(None))

    # Ownership scope: admin/manager ("heads") see every contact. Everyone else
    # only sees leads they currently own (assigned_to) or originally brought in
    # (created_by) — the same rule services/lead_access.py already enforces for
    # writing to a lead. `user` was previously accepted here and never used,
    # which let any signed-in Sales Executive list/export every contact
    # company-wide. This is the fix for that.
    if not is_head(user):
        q = q.filter(or_(Contact.assigned_to == user.id, Contact.created_by == user.id))

    # Apply each filter only if it was provided (avoids unnecessary WHERE clauses)
    if "contact_type" in f:
        q = q.filter(Contact.contact_type == f["contact_type"].value)
    if "partner_type" in f:
        q = q.join(PartnerProfile, PartnerProfile.contact_id == Contact.id).filter(
            PartnerProfile.data["partner_type"].as_string() == f["partner_type"]
        )
    if "branch_id" in f:
        q = q.filter(Contact.branch_id == f["branch_id"])
    if "district" in f:
        q = q.filter(Contact.district.ilike(f"%{f['district']}%"))
    if "search" in f:
        like = f"%{f['search'].strip()}%"
        q = q.filter(or_(Contact.name.ilike(like), Contact.phone.ilike(like)))
    if "lead_source_id" in f:
        q = q.filter(Contact.lead_source_id == f["lead_source_id"])
    if "assigned_to" in f:
        q = q.filter(Contact.assigned_to == f["assigned_to"])
    if "created_by" in f:
        q = q.filter(Contact.created_by == f["created_by"])
    if f.get("unattributed") is True:
        q = q.filter(Contact.created_by.is_(None))
    if f.get("verified") is True:
        q = q.filter(verified_clause())
    elif f.get("verified") is False:
        q = q.filter(~verified_clause())
    # created_from / created_to are IST calendar dates from the client.
    # The DB stores UTC, so midnight IST = 18:30 UTC the previous day.
    # ist_day_start_utc() converts a local date to the correct UTC boundary.
    if "created_from" in f:
        q = q.filter(Contact.created_at >= ist_day_start_utc(f["created_from"]))
    if "created_to" in f:
        q = q.filter(Contact.created_at < ist_day_start_utc(f["created_to"] + timedelta(days=1)))
    return q


# Sort orders for the "Sort by" control in the contacts list.
# Each value is a tuple of SQLAlchemy ORDER BY expressions.
# The secondary sort on `id` ensures stable pagination — without it, two rows with
# the same created_at or name could swap positions between pages.
CONTACT_ORDERINGS = {
    "newest": (Contact.created_at.desc(), Contact.id.desc()),
    "oldest": (Contact.created_at.asc(), Contact.id.asc()),
    "name_asc": (func.lower(Contact.name).asc(), Contact.id.asc()),
    "name_desc": (func.lower(Contact.name).desc(), Contact.id.desc()),
}


@router.get("", response_model=ContactListResponse, summary="List contacts")
def list_contacts(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    sort: Literal["newest", "oldest", "name_asc", "name_desc"] = Query("newest"),
    f: dict = Depends(contact_filters),
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /contacts — Paged list of contacts with filters and sort.

    HOW PAGINATION WORKS:
        `skip=0&limit=10` → first 10 rows
        `skip=10&limit=10` → next 10 rows
        The response always includes `total` so the UI can show page numbers.

    THREE QUERIES PER REQUEST:
        1. COUNT query — runs without offset/limit to get the total matching count.
        2. DATA query  — runs with offset/limit and joinedload to fetch the page.
        3. STAGE query — current pipeline stage of every contact on the page, in one go.

    WHY joinedload?
        Without it, SQLAlchemy would run a separate SELECT for every relationship
        on every row (N+1 query problem). joinedload fetches related rows in one
        JOIN, which is much faster for a page of 10–100 contacts.

    """
    # 1. Total count (ignores skip/limit so the UI knows the full result set size)
    total = contact_query(db, f, current_user).count()

    # 2. Page data
    rows = (
        contact_query(db, f, current_user)
        .options(
            joinedload(Contact.lead_source),
            joinedload(Contact.assigned_employee),
            joinedload(Contact.creator),
            joinedload(Contact.partner_profile),
            joinedload(Contact.consumer_profile),
            joinedload(Contact.branch),
        )
        .order_by(*CONTACT_ORDERINGS[sort])
        .offset(skip)
        .limit(limit)
        .all()
    )

    # 3. Build the response list
    stages = _current_stages(db, [contact.id for contact in rows])
    items = []
    for contact in rows:
        pending = missing_fields(contact)
        item = ContactListItem.model_validate(
            {
                "id": contact.id,
                "contact_type": contact.contact_type,
                # Branch relationship object (id, name, code); None if unassigned
                "branch": contact.branch,
                "name": contact.name,
                "phone": contact.phone,
                "address": contact.address,
                "district": contact.district,
                # Type-specific headline fields pulled from JSONB profile data
                "partner_type": (contact.partner_profile.data or {}).get("partner_type") if contact.partner_profile else None,
                "house_stage": (contact.consumer_profile.data or {}).get("house_stage") if contact.consumer_profile else None,
                "work_area": (contact.partner_profile.data or {}).get("work_area") if contact.partner_profile else None,
                "lead_stage": stages.get(contact.id),
                "lead_source": contact.lead_source,
                "assigned_to": contact.assigned_employee,
                "created_by": contact.creator,
                # Sourced from the contact's own branch_id (set at creation time), NOT the
                # creator's current branch — an employee may move branches after creating this contact.
                "created_by_branch": contact.branch.code if contact.branch else None,
                "created_at": contact.created_at,
                "is_verified": not pending,
                "missing_fields": pending,
            },
            from_attributes=True,
        )
        items.append(item)

    return ContactListResponse(items=items, total=total, skip=skip, limit=limit)



@router.get("/partner-types", summary="List partner profession types")
def list_profession_types(db: Session = Depends(get_tenant_db)):
    """Active partner types from contact_field_definitions (entity_type='partner', field_name='partner_type')."""
    from models.contact_field_definition import ContactFieldDefinition
    defn = db.query(ContactFieldDefinition).filter(
        ContactFieldDefinition.entity_type == "partner",
        ContactFieldDefinition.field_name == "partner_type",
        ContactFieldDefinition.is_active.is_(True),
    ).first()
    if defn and defn.options:
        return [{"value": v, "label": v} for v in defn.options]
    return []


@router.get("/export", summary="Export contacts")
def export_contacts(
    format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
    f: dict = Depends(contact_filters),
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    """Downloads the contacts matching the list filters as CSV, Excel or PDF (`format`).
    It uses the same filters as the list, so the export matches what is on screen.
    """
    total = contact_query(db, f, current_user).count()
    limit = contact_export.MAX_ROWS[format]
    if total > limit:
        raise HTTPException(
            status_code=400,
            detail=f"{total} contacts match; a {format.upper()} export holds at most {limit}. Narrow the filters.",
        )
    rows = (
        contact_query(db, f, current_user)
        .options(
            joinedload(Contact.lead_source),
            joinedload(Contact.assigned_employee),
            joinedload(Contact.creator),
            joinedload(Contact.partner_profile),
            joinedload(Contact.consumer_profile),
        )
        .order_by(Contact.created_at.desc(), Contact.id.desc())
        .all()
    )
    export_headers, c_fields, p_fields = contact_export.get_export_config(db)
    table = contact_export.build_rows([(c, None, not missing_fields(c)) for c in rows], c_fields, p_fields)
    if format == "csv":
        body = contact_export.to_csv(table, export_headers)
    elif format == "xlsx":
        body = contact_export.to_xlsx(table, export_headers)
    else:
        summary = ", ".join(f"{k.replace('_', ' ')}: {getattr(v, 'value', v)}" for k, v in f.items())
        body = contact_export.to_pdf(table, export_headers, summary)
    filename = f"contacts_{date.today():%Y%m%d}.{format}"
    return Response(
        body,
        media_type=contact_export.MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/import/template", summary="Download import template")
def import_template(
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    A blank CSV with core headers plus the tenant's own profile field columns
    (loaded from contact_field_definitions), so the user sees their exact field names.
    """
    field_defs = contact_import.load_field_defs(db)
    return Response(
        contact_import.template_csv(field_defs),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="contacts_import_template.csv"'},
    )


# ── Import analyze (dry run) ───────────────────────────────────────────────────
# IMPORTANT: these /import/* routes MUST stay ABOVE /{contact_id} in this file.
# FastAPI matches in registration order; without that, "import" would match the
# int param and return 422 "Contact id must be a whole number".

@router.post("/import/analyze", summary="Dry-run a contact import file")
def import_analyze(
    file: UploadFile = File(...),
    default_type: str = Form("consumer"),
    default_source_id: int | None = Form(None),
    duplicates: str = Form("skip"),
    mapping: str | None = Form(None),
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Read an uploaded CSV/XLSX, validate every row, and return a preview report
    WITHOUT writing anything to the database.

    HOW IT WORKS:
        1. Read the file bytes and parse rows.
        2. Auto-detect the header row unless `mapping` JSON is provided.
        3. Run `contact_import.analyze()` — validates phones, names, sources,
           house stages, partner types, and duplicate detection.
        4. Return a summary + first 20 row-level results so the UI can show
           the user what will happen before they commit.

    FORM FIELDS:
        file              — .csv or .xlsx upload
        default_type      — "consumer" or "partner" (default: "consumer")
        default_source_id — lead source id to apply when the sheet has none
        duplicates        — "skip" or "update_blank"
        mapping           — JSON object {"0": "name", "1": "phone", ...} (column index → field name)
                            omit to auto-detect

    RETURNS:
        {
          "summary": { "total": 50, "ready": 40, "warning": 5, "duplicate": 3, "error": 2 },
          "header_row": 0,
          "mapping": {"0": "name", "1": "phone"},
          "rows": [ { "row": 2, "status": "ready", "issues": [], "data": {...} }, ... ]
        }
    """
    content = file.file.read()
    try:
        rows = contact_import.read_rows(file.filename or "", content)
    except contact_import.ImportFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Load field defs once — reused for column detection, validation, and analysis
    field_defs = contact_import.load_field_defs(db)

    if mapping:
        import json as _json
        try:
            raw_map = _json.loads(mapping)
            col_map: dict[int, str] = {int(k): v for k, v in raw_map.items()}
            header_row = 0
        except Exception:
            raise HTTPException(status_code=422, detail="mapping must be a JSON object like {\"0\": \"name\", \"1\": \"phone\"}")
    else:
        # detect_all_columns maps both core fields AND profile fields from contact_field_definitions
        header_row, col_map = contact_import.detect_all_columns(rows, field_defs)

    opts = contact_import.Options(
        default_type=default_type,
        default_source_id=default_source_id,
        duplicates=duplicates,
    )
    results = contact_import.analyze(db, rows, header_row, col_map, opts, field_defs=field_defs)
    summary = contact_import.summarize(results)

    # ── Build the columns array the frontend ImportReport type expects ─────────
    # For each column in the header row, include its header text, the detected
    # field mapping (or null), and up to 3 sample values from the data rows.
    header_cells = rows[header_row] if header_row < len(rows) else []
    num_cols = max((len(r.raw) for r in results), default=len(header_cells))

    # Collect samples per column index from the first few data rows
    samples: dict[int, list[str]] = {}
    for r in results[:10]:
        for col_idx, cell in enumerate(r.raw):
            if cell and cell.strip():
                samples.setdefault(col_idx, [])
                if len(samples[col_idx]) < 3:
                    samples[col_idx].append(cell.strip())

    columns = [
        {
            "index": col_idx,
            "header": header_cells[col_idx] if col_idx < len(header_cells) else f"Column {col_idx + 1}",
            "field": col_map.get(col_idx),  # None when unmatched
            "sample": samples.get(col_idx, []),
        }
        for col_idx in range(num_cols)
    ]

    # ── Build the fields dict: field_name → display label ─────────────────────
    # Core fields
    fields: dict[str, str] = dict(contact_import.FIELDS)
    # Profile fields from this tenant's contact_field_definitions
    for entity_defs in field_defs.values():
        for defn in entity_defs:
            fields[defn.field_name] = defn.field_label or defn.field_name

    # ── Required fields that have no column mapped yet ─────────────────────────
    # "phone" is the only hard-required field (name and district become warnings).
    required = {"phone"}
    mapped_fields = set(col_map.values())
    missing_required = [f for f in required if f not in mapped_fields]

    # ── Problem rows only (warning, duplicate, error) ─────────────────────────
    # The frontend only displays rows that need attention; "ready" rows are silent.
    problem_rows = [
        {
            "row": r.row,
            "status": r.status,
            "issues": r.issues,
            "raw": r.raw,
            # Extract name and phone from validated data for the row preview
            "name": r.data.get("name"),
            "phone": r.data.get("phone"),
        }
        for r in results
        if r.status != "ready"
    ]

    return {
        "header_row": header_row,
        "columns": columns,
        "fields": fields,
        "missing_required": missing_required,
        "summary": summary,
        "rows": problem_rows,
    }


@router.post("/import/commit", summary="Write a contact import to the database")
def import_commit(
    file: UploadFile = File(...),
    default_type: str = Form("consumer"),
    default_source_id: int | None = Form(None),
    duplicates: str = Form("skip"),
    mapping: str = Form(...),
    assigned_to: str | None = Form(None),
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Re-analyze the same file and write all ready/warning rows to the database.

    WHY RE-ANALYZE?
        The frontend calls analyze first (dry run) so the user can preview.
        On commit it re-uploads the same file and we re-run analysis so the
        preview and the actual import always agree — there is no stale state.

    FORM FIELDS:
        file              — same .csv or .xlsx as analyze
        default_type      — "consumer" or "partner"
        default_source_id — fallback lead source id
        duplicates        — "skip" or "update_blank"
        mapping           — required JSON column→field map from the analyze response
        assigned_to       — one employee id ("5") or a JSON array ("[3,5,7]"), or
                            null to auto-pick from the active sales staff

    RETURNS:
        { "created": 40, "updated": 5, "skipped": 5 }
    """
    import json as _json

    content = file.file.read()
    try:
        rows = contact_import.read_rows(file.filename or "", content)
    except contact_import.ImportFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        raw_map = _json.loads(mapping)
        col_map: dict[int, str] = {int(k): v for k, v in raw_map.items()}
    except Exception:
        raise HTTPException(status_code=422, detail="mapping must be a JSON object like {\"0\": \"name\", \"1\": \"phone\"}")

    # Resolve assignee ids — use the provided list or fall back to active sales staff
    if assigned_to:
        try:
            parsed = _json.loads(assigned_to)
            # The web import form sends one id ("5"); other callers send an array ("[3,5]").
            assignee_ids: list[int] = parsed if isinstance(parsed, list) else [parsed]
            if not assignee_ids or not all(type(item) is int for item in assignee_ids):
                raise ValueError
        except ValueError:
            raise HTTPException(status_code=422, detail="assigned_to must be an employee id or a JSON array of employee ids")
    else:
        # The role lives in the roles table; employees only hold role_id.
        sales_staff = (
            db.query(Employee.id)
            .join(Role, Role.id == Employee.role_id)
            .filter(Role.slug == "sales_executive", Employee.is_active == 1, Employee.deleted_at.is_(None))
            .all()
        )
        assignee_ids = [row[0] for row in sales_staff]
        if not assignee_ids:
            raise HTTPException(status_code=422, detail="No active sales staff found; pass assigned_to explicitly")

    opts = contact_import.Options(
        default_type=default_type,
        default_source_id=default_source_id,
        duplicates=duplicates,
    )
    # Load field defs for validation — same query as in analyze to keep both in sync
    field_defs = contact_import.load_field_defs(db)
    header_row, _ = contact_import.detect_all_columns(rows, field_defs)
    results = contact_import.analyze(db, rows, header_row, col_map, opts, field_defs=field_defs)
    return contact_import.run_import(db, results, opts, assignee_ids, created_by=current_user.id)


@router.get("/type-counts", summary="Count contacts per type")
def contact_type_counts(
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    """A live count of the contacts per type (consumer, partner), for the tab badges."""
    q = db.query(Contact.contact_type, func.count(Contact.id)).filter(Contact.deleted_at.is_(None))
    rows = q.group_by(Contact.contact_type).all()
    counts = {"consumer": 0, "partner": 0}
    for kind, total in rows:
        counts[str(getattr(kind, "value", kind))] = total
    return {"all": sum(counts.values()), **counts}


# ── Stats ─────────────────────────────────────────────────────────────────────
# IMPORTANT: these fixed-path routes MUST come before /{contact_id}.
# FastAPI matches routes in registration order; "stats" would otherwise be
# tested against contact_id: int and fail before reaching this handler.

@router.get("/stats", response_model=ContactStatsResponse, summary="Unassigned contact counts by lead source")
def contact_stats(current_user: Employee = Depends(get_current_user)):
    """
    Return the total number of unassigned contacts and a breakdown by lead source.

    WHY THIS EXISTS:
        The Bulk Assign modal needs to know how many unassigned contacts exist per
        source so the user can decide how many to distribute to each employee.

    RETURNS:
        {
          "total_unassigned": 42,
          "unassigned_by_source": {"WhatsApp": 15, "Website": 10, "Walk-in": 17}
        }
    """
    db = current_user._db
    try:
        total = db.scalar(
            select(func.count(Contact.id)).where(
                Contact.deleted_at.is_(None),
                Contact.assigned_to.is_(None),
            )
        ) or 0

        rows = db.execute(
            select(LeadSource.name, func.count(Contact.id))
            .join(LeadSource, Contact.lead_source_id == LeadSource.id, isouter=True)
            .where(Contact.deleted_at.is_(None), Contact.assigned_to.is_(None))
            .group_by(LeadSource.name)
        ).all()
        by_source = {(row[0] or "Unknown"): row[1] for row in rows}
    except OperationalError as exc:
        logger.error("contact_stats — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")
    return ContactStatsResponse(total_unassigned=total, unassigned_by_source=by_source)


# ── Bulk assign ───────────────────────────────────────────────────────────────

@router.post("/bulk-assign", response_model=ContactBulkAssignResponse, summary="Bulk assign unassigned contacts by source")
def bulk_assign_contacts(
    body: ContactBulkAssignRequest,
    current_user: Employee = Depends(get_current_user),
):
    """
    Assign batches of unassigned contacts to employees by lead source.

    HOW IT WORKS:
        For each rule {source_id, assigned_to, count}:
          1. Find the N oldest unassigned contacts from that source (or any source
             when source_id is None), ordered oldest-first so early enquiries
             get picked up first.
          2. UPDATE their assigned_to to the given employee id.

    BODY EXAMPLE:
        {
          "assignments": [
            { "source_id": 3, "assigned_to": 5, "count": 15 },
            { "source_id": null, "assigned_to": 7, "count": 10 }
          ]
        }

    Returns the total number of contacts actually assigned (may be less than
    requested when fewer unassigned contacts exist for that source).
    """
    db = current_user._db
    try:
        total_assigned = 0

        for rule in body.assignments:
            query = (
                select(Contact.id)
                .where(Contact.deleted_at.is_(None), Contact.assigned_to.is_(None))
                .order_by(Contact.created_at.asc())
                .limit(rule.count)
            )
            if rule.source_id is not None:
                query = query.where(Contact.lead_source_id == rule.source_id)

            contact_ids = [row[0] for row in db.execute(query).all()]
            if not contact_ids:
                continue

            db.execute(
                sa_update(Contact)
                .where(Contact.id.in_(contact_ids))
                .values(
                    assigned_to=rule.assigned_to,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            total_assigned += len(contact_ids)

        try:
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            logger.error("bulk_assign_contacts — commit failed: %s", exc)
            raise HTTPException(503, detail="Could not save. Please try again.")

    except OperationalError as exc:
        logger.error("bulk_assign_contacts — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")

    return ContactBulkAssignResponse(assigned=total_assigned)


@router.get("/{contact_id}", response_model=ContactResponse, summary="Get a contact")
def get_contact(
    contact_id: int,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /contacts/{id} — Full detail for one contact.

    The contact relationships are eagerly loaded so response serialization does not
    issue hidden lazy-load queries.
    """
    contact = db.scalar(
        select(Contact)
        .options(
            joinedload(Contact.assigned_employee),
            joinedload(Contact.creator),
            joinedload(Contact.lead_source),
            joinedload(Contact.referred_by),
            joinedload(Contact.consumer_profile),
            joinedload(Contact.partner_profile),
        )
        .where(
            Contact.id == contact_id,
            Contact.deleted_at.is_(None),
        )
    )

    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Same ownership rule as the list: a non-head employee can only open a
    # contact they own or created. Previously this endpoint had no check at
    # all — any signed-in employee could open any contact by id.
    if not is_head(current_user) and contact.assigned_to != current_user.id and contact.created_by != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Only the assigned employee, the creator, or an admin or manager can view this contact",
        )

    response = ContactResponse.model_validate(contact, from_attributes=True)

    # How many contacts does this person refer? Requires a COUNT — set it explicitly.
    response.referrals_count = db.scalar(
        select(func.count(Contact.id)).where(
            Contact.referred_by_id == contact_id,
            Contact.deleted_at.is_(None),
        )
    ) or 0

    response.missing_fields = missing_fields(contact)
    response.is_verified = not response.missing_fields

    return response


@router.post("", response_model=ContactResponse, status_code=201, summary="Create a contact")
def create_contact(
    body: ContactCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
    platform: str = Depends(client_platform),
):
    """
    POST /contacts — Create a new contact (consumer or partner).

    CREATION FLOW:
        1. Mobile-only validation: mobile app must always send address + district.
        2. Validate lead source, GPS rules, referrer, profile blocks, and phone uniqueness.
        3. Build the Contact ORM object.
        4. Attach the type-specific profile (consumer/partner) if provided.
        5. db.commit() persists the contact atomically.
        6. db.refresh(contact) reloads the contact from DB so relationships are populated.
        7. Build and return the response.

    ASSIGNMENT BEHAVIOUR:
        - body.assigned_to provided → validate employee exists, assign to them.
        - body.assigned_to omitted  → contact is created UNASSIGNED (assigned_to = NULL).
          A manager reviews new leads and assigns them manually.
    """
    # Step 1 (removed): address and district are no longer required at create time.
    # They are required for verification — the contact will show as unverified until filled.

    # Step 2: validate all business rules before touching the DB
    source = db.query(LeadSource).filter(LeadSource.id == body.lead_source_id).first()
    if not source:
        raise HTTPException(status_code=400, detail="Lead source not found")
    _ensure_referrer(db, body.referred_by_id)
    _check_type_profiles(body.contact_type, body.consumer_profile, body.partner_profile)
    if body.partner_profile is not None:
        _validate_partner_profile_data(db, body.partner_profile.data)
    if body.consumer_profile is not None:
        _validate_consumer_profile_data(db, body.consumer_profile.data)
    # partner_profile is now optional at create time — profession_type + work_area are required for verification.
    _ensure_phone_free(db, body.phone, current_user)

    # Step 2b: validate assignee if provided
    assigned_to: int | None = None
    if body.assigned_to is not None:
        if not db.query(Employee).filter(Employee.id == body.assigned_to).first():
            raise HTTPException(status_code=400, detail="Assigned employee not found")
        assigned_to = body.assigned_to

    # Step 3: build the Contact row.
    # model_dump() converts the Pydantic object to a plain dict; we exclude profile
    # blocks and assigned_to because they are handled separately below.
    data = body.model_dump(exclude={"consumer_profile", "partner_profile", "assigned_to"})
    contact = Contact(**data, phone_key=phone_key(body.phone), assigned_to=assigned_to, created_by=current_user.id)

    # Step 4: attach type-specific profile in the same transaction
    if body.consumer_profile is not None:
        contact.consumer_profile = ConsumerProfile(**body.consumer_profile.model_dump(mode="json"))
    if body.partner_profile is not None:
        contact.partner_profile = PartnerProfile(**body.partner_profile.model_dump(mode="json"))

    auto_followup_needed = getattr(current_user, "_role_slug", None) in NON_ASSIGNABLE_ROLES

    db.add(contact)
    db.commit()
    db.refresh(contact)  # Reload so relationships (lead_source, assigned_employee, etc.) are populated

    # Notifications fire after commit so contact.id is guaranteed persisted
    def _notify_created():
        from services.notification_service import create_notification, notify_roles
        notify_roles(
            db, ["admin", "manager"], "contact_created",
            f"New contact: {contact.name}",
            f"{current_user.name} added a new {contact.contact_type} contact.",
            "contact", contact.id,
            background_tasks=background_tasks,
        )
        if contact.assigned_to and contact.assigned_to != current_user.id:
            create_notification(
                db, contact.assigned_to, "contact_assigned",
                f"Contact assigned to you: {contact.name}",
                f"{contact.name} ({contact.contact_type}) has been assigned to you.",
                "contact", contact.id,
                background_tasks=background_tasks,
            )
    _run_notification(db, _notify_created)

    # Steps 9–10: build response
    response = ContactResponse.model_validate(contact, from_attributes=True)
    response.referrals_count = 0  # Brand new contact cannot have referrals yet
    response.missing_fields = missing_fields(contact)
    response.is_verified = not response.missing_fields
    response.auto_followup_needed = auto_followup_needed
    return response


@router.put("/{contact_id}", response_model=ContactResponse, summary="Update a contact")
def update_contact(
    contact_id: int,
    body: ContactUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
    platform: str = Depends(client_platform),
):
    """
    PUT /contacts/{id} — Partial update of a contact.

    KEY CONCEPT — PARTIAL UPDATE:
        ContactUpdate has every field optional. `body.model_fields_set` is the set
        of keys the caller actually sent. We ONLY update those fields.
        If `name` is not in the request body, `contact.name` stays unchanged.

        `model_dump(exclude_unset=True)` returns only the fields that were explicitly
        provided, not the defaults. This is how the partial update is implemented.

    PROFILE UPSERT:
        - If the contact already has a profile, we merge the sent fields into it.
        - If the contact has no profile yet, we create one. In that case, required
          fields (profession_type for partners) must be present.

    FIELDS THAT CANNOT BE CLEARED:
        contact_type, name, phone, and assigned_to are required once set.
        If the caller sends `null` for these, we skip the update (ignore it).
        This prevents accidentally wiping critical fields via a partial update.
    """
    contact = get_live_contact(db, contact_id)

    if body.assigned_to is not None:
        if not db.query(Employee).filter(Employee.id == body.assigned_to).first():
            raise HTTPException(status_code=400, detail="Assigned employee not found")

    _ensure_referrer(db, body.referred_by_id, contact_id)

    # `fields` is the set of keys the caller actually included in the request
    fields = body.model_fields_set

    # Re-validate lead source if it was changed
    if "lead_source_id" in fields and body.lead_source_id is not None:
        if not db.query(LeadSource).filter(LeadSource.id == body.lead_source_id).first():
            raise HTTPException(status_code=400, detail="Lead source not found")

    # Use the new type if being changed, otherwise the existing type from the DB
    final_type = body.contact_type.value if body.contact_type is not None else contact.contact_type
    _check_type_profiles(final_type, body.consumer_profile, body.partner_profile)
    if body.consumer_profile is not None and body.consumer_profile.data:
        _validate_consumer_profile_data(db, body.consumer_profile.data)

    if "phone" in fields and body.phone is not None:
        _ensure_phone_free(db, body.phone, current_user, exclude_id=contact.id)

    # Build a dict of only the fields to apply (excludes profiles, handled separately)
    update_data = body.model_dump(exclude_unset=True, exclude={"consumer_profile", "partner_profile"})
    # Do not allow clearing required fields by sending null
    for key in ("contact_type", "name", "phone", "assigned_to"):
        if key in update_data and update_data[key] is None:
            del update_data[key]

    previous_owner = contact.assigned_to
    for field, value in update_data.items():
        setattr(contact, field, value)

    new_owner = update_data.get("assigned_to")

    # If the phone was updated, recalculate the phone_key for duplicate detection
    if "phone" in update_data:
        contact.phone_key = phone_key(update_data["phone"])

    # --- Profile upsert logic ---

    if body.consumer_profile is not None:
        profile_data = body.consumer_profile.model_dump(exclude_unset=True, mode="json")
        new_data = profile_data.get("data") or {}
        if contact.consumer_profile is None:
            contact.consumer_profile = ConsumerProfile(data=new_data)
        else:
            # Merge incoming keys into the existing JSONB dict (only sent keys change)
            contact.consumer_profile.data = {**(contact.consumer_profile.data or {}), **new_data}

    if body.partner_profile is not None:
        profile_data = body.partner_profile.model_dump(exclude_unset=True, mode="json")
        new_data = profile_data.get("data") or {}
        if new_data:
            _validate_partner_profile_data(db, new_data)
        if contact.partner_profile is None:
            if not new_data.get("partner_type"):
                raise HTTPException(
                    status_code=400, detail="partner_type is required for a partner"
                )
            contact.partner_profile = PartnerProfile(data=new_data)
        else:
            # Merge incoming keys into the existing JSONB dict (only sent keys change)
            contact.partner_profile.data = {**(contact.partner_profile.data or {}), **new_data}

    db.add(contact)
    db.commit()
    db.refresh(contact)

    if "assigned_to" in update_data and new_owner != previous_owner and new_owner:
        def _notify_assignee():
            from services.notification_service import create_notification
            create_notification(
                db, new_owner, "contact_assigned",
                f"Contact assigned to you: {contact.name}",
                f"{contact.name} ({contact.contact_type}) has been assigned to you.",
                "contact", contact.id,
                background_tasks=background_tasks,
            )
        _run_notification(db, _notify_assignee)

    response = ContactResponse.model_validate(contact, from_attributes=True)
    response.referrals_count = (
        db.query(func.count(Contact.id))
        .filter(Contact.referred_by_id == contact_id)
        .scalar() or 0
    )
    response.missing_fields = missing_fields(contact)
    response.is_verified = not response.missing_fields

    return response


@router.delete("/{contact_id}", status_code=204, summary="Delete a contact")
def delete_contact(
    contact_id: int,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    DELETE /contacts/{id} — Soft-delete a contact.

    WHAT IS A SOFT DELETE?
        Instead of removing the row from the database, we set `deleted_at` to the
        current time. Every other query filters `deleted_at IS NULL`, so the contact
        disappears from all lists. But the row — and all its stage history, follow-up
        logs, and assignment records — is preserved forever.

        This is important for audit trails: a manager can always look up what happened
        to a lead even after it was "deleted".

    Returns 204 No Content on success (standard for DELETE with no response body).
    Returns 404 if the contact is already deleted or does not exist.
    """
    contact = get_live_contact(db, contact_id)

    contact.deleted_at = datetime.now(timezone.utc)
    db.add(contact)
    db.commit()


# ── Stage endpoints ───────────────────────────────────────────────────────────

_LOCKED_TERMINAL = frozenset({"lost", "inactive", "inactive_dealer"})


def _current_stage(db: Session, contact_id: int) -> LeadStage | None:
    return (
        db.query(LeadStage)
        .filter(LeadStage.contact_id == contact_id)
        .order_by(LeadStage.changed_at.desc(), LeadStage.id.desc())
        .first()
    )


def _current_stages(db: Session, contact_ids: list[int]) -> dict[int, ContactStageResponse]:
    """Current stage per contact id, using the same "newest log row" rule as `_current_stage`."""
    if not contact_ids:
        return {}
    rows = (
        db.query(LeadStage, PipelineStage)
        .join(PipelineStage, PipelineStage.id == LeadStage.stage_id)
        .filter(LeadStage.contact_id.in_(contact_ids))
        .order_by(LeadStage.changed_at.desc(), LeadStage.id.desc())
        .all()
    )
    current: dict[int, ContactStageResponse] = {}
    for log, stage in rows:
        # Rows are newest first, so the first one seen for a contact wins.
        current.setdefault(log.contact_id, ContactStageResponse(
            stage_id=stage.id,
            stage_slug=stage.slug,
            stage_name=stage.name,
            contact_type=stage.contact_type,
            changed_at=log.changed_at,
        ))
    return current


@router.get("/{contact_id}/stage", response_model=ContactStageResponse, summary="Current stage of a contact")
def get_contact_stage(
    contact_id: int,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    contact = get_live_contact(db, contact_id)
    from services.lead_access import ensure_can_work_contact
    ensure_can_work_contact(current_user, contact)
    row = _current_stage(db, contact_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No stage set for this contact")
    stage = db.query(PipelineStage).filter(PipelineStage.id == row.stage_id).first()
    if not stage:
        raise HTTPException(status_code=404, detail="Stage not found")
    return ContactStageResponse(
        stage_id=stage.id,
        stage_slug=stage.slug,
        stage_name=stage.name,
        contact_type=stage.contact_type,
        changed_at=row.changed_at,
    )


@router.post("/{contact_id}/stage", response_model=ContactStageResponse, status_code=201, summary="Move contact to a stage")
def set_contact_stage(
    contact_id: int,
    body: ContactStageSet,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    contact = get_live_contact(db, contact_id)
    from services.lead_access import ensure_can_work_contact
    ensure_can_work_contact(current_user, contact)

    stage = db.query(PipelineStage).filter(
        PipelineStage.id == body.stage_id,
        PipelineStage.is_active == 1,
    ).first()
    if not stage:
        raise HTTPException(status_code=404, detail="Pipeline stage not found or inactive")
    if stage.contact_type != contact.contact_type:
        raise HTTPException(status_code=400, detail=f"Stage belongs to {stage.contact_type} pipeline, not {contact.contact_type}")

    if stage.slug in _LOCKED_TERMINAL and not body.loss_reason:
        raise HTTPException(status_code=422, detail="loss_reason is required when moving to this stage")

    current = _current_stage(db, contact_id)
    old_slug = None
    if current:
        old_stage = db.query(PipelineStage).filter(PipelineStage.id == current.stage_id).first()
        old_slug = old_stage.slug if old_stage else None
        if current.stage_id == body.stage_id:
            raise HTTPException(status_code=409, detail="Contact is already in this stage")

    row = LeadStage(
        contact_id=contact_id,
        stage_id=body.stage_id,
        changed_by=current_user.id,
        loss_reason=body.loss_reason,
        loss_notes=body.loss_notes,
        awareness_note=body.awareness_note,
    )
    db.add(row)
    add_log(db, "contact", contact_id, "stage_changed", current_user.id, old_slug, stage.slug)
    db.commit()
    db.refresh(row)
    return ContactStageResponse(
        stage_id=stage.id,
        stage_slug=stage.slug,
        stage_name=stage.name,
        contact_type=stage.contact_type,
        changed_at=row.changed_at,
    )


# ── Timeline ──────────────────────────────────────────────────────────────────

_TIMELINE_LABELS = {
    "created": "Contact created",
    "edited": "Contact edited",
    "stage_changed": "Stage changed",
    "assigned": "Contact assigned",
    "activity_logged": "Activity logged",
    "task_created": "Follow-up scheduled",
    "task_completed": "Follow-up completed",
    "task_status_changed": "Follow-up status changed",
    "task_rescheduled": "Follow-up rescheduled",
    "task_reassigned": "Follow-up reassigned",
    "task_deleted": "Follow-up deleted",
    "log_deleted": "Activity deleted",
}


@router.get("/{contact_id}/timeline", summary="History of a contact, newest first")
def contact_timeline(
    contact_id: int,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    from sqlalchemy import select as sa_select
    from services.lead_access import ensure_can_work_contact
    contact = get_live_contact(db, contact_id)
    ensure_can_work_contact(current_user, contact)

    rows = db.scalars(
        sa_select(RecordLog)
        .where(RecordLog.entity_type == "contact", RecordLog.entity_id == contact_id)
        .order_by(RecordLog.created_at.desc(), RecordLog.id.desc())
    ).all()

    assignee_actions = {"assigned", "task_reassigned"}
    ids = {r.actor_id for r in rows if r.actor_id}
    ids |= {int(r.new_value) for r in rows if r.action in assignee_actions and r.new_value and r.new_value.isdigit()}
    names: dict[int, str] = {}
    if ids:
        from sqlalchemy import select as _sel
        names = {r.id: r.name for r in db.execute(_sel(Employee.id, Employee.name).where(Employee.id.in_(ids))).all()}

    stage_actions = {"stage_changed"}
    stage_ids_needed = {int(r.new_value) for r in rows if r.action in stage_actions and r.new_value and r.new_value.isdigit()}
    stage_names: dict[str, str] = {}
    if stage_ids_needed:
        pass  # new_value already holds the slug string from add_log

    def _detail(r: RecordLog) -> str | None:
        if r.action in assignee_actions:
            if not r.new_value:
                return "Unassigned"
            emp = names.get(int(r.new_value)) if r.new_value.isdigit() else None
            return f"Assigned to {emp}" if emp else "Assigned to an employee"
        if r.action == "stage_changed":
            parts = []
            if r.old_value:
                parts.append(r.old_value.replace("_", " ").capitalize())
            if r.new_value:
                parts.append(r.new_value.replace("_", " ").capitalize())
            return " → ".join(parts) if parts else None
        return None

    return [
        {
            "id": r.id,
            "action": r.action,
            "label": _TIMELINE_LABELS.get(r.action, r.action.replace("_", " ").capitalize()),
            "detail": _detail(r),
            "actor_name": names.get(r.actor_id),
            "old_value": r.old_value,
            "new_value": r.new_value,
            "created_at": r.created_at,
        }
        for r in rows
    ]
