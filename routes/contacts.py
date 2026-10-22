"""
Contact routes — all HTTP endpoints for creating, reading, updating and deleting contacts.

WHAT IS THIS FILE?
------------------
This is a FastAPI "router" module. It defines every HTTP endpoint that starts with
`/contacts`. FastAPI reads the decorators (`@router.get`, `@router.post`, etc.) and
registers them when the app starts.

HOW FASTAPI ROUTES WORK (simple version):
    @router.get("/{contact_id}")   →   GET /contacts/42
    def get_contact(contact_id: int, db = Depends(get_db)):
        ...                            FastAPI injects `db` automatically via Depends()

    The `Depends(get_current_user)` on the router itself means EVERY endpoint in this
    file requires a valid JWT token. Anonymous requests get a 401 automatically.

HELPER FUNCTIONS IN THIS FILE:
    _stage_response()       — build a LeadStageResponse with stage slug/name filled in
    _get_new_lead_stage()   — fetch the "new_lead" PipelineStage for a contact type
    _pick_random_assignee() — choose a random eligible employee for auto-assignment
    _ensure_phone_free()    — raise 409 if the phone already belongs to another contact
    _ensure_referrer()      — validate that the referring contact exists and is not self
    _check_location()       — enforce GPS rules for offline vs. non-offline leads
    _check_type_profiles()  — ensure the profile block matches the contact type
    _check_consumer_options()— validate house_stage / tank_type against Settings

ROUTE OVERVIEW:
    GET    /contacts                  — paged list with filters
    GET    /contacts/export           — download CSV / XLSX / PDF
    GET    /contacts/import/template  — download blank import CSV
    POST   /contacts/import/analyze   — dry-run an upload (no writes)
    POST   /contacts/import/commit    — write the upload to the DB
    GET    /contacts/type-counts      — badge counts per type
    GET    /contacts/partner-types    — active partner profession options
    GET    /contacts/{id}             — single contact detail
    POST   /contacts                  — create a new contact
    PUT    /contacts/{id}             — update a contact
    DELETE /contacts/{id}             — soft-delete a contact
    GET    /contacts/{id}/stages      — full pipeline history
    POST   /contacts/{id}/stages      — move contact to a new stage
"""

from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, aliased, joinedload
import json

import contact_export
import contact_import
from business_time import ist_day_start_utc
from auth import get_current_user
from contact_lookup import get_live_contact
from database import get_db
from phone_utils import phone_key
from request_context import client_platform
from verification import missing_fields, verified_clause
from models import Contact, ConsumerProfile, ContactAssignment, DealerProfile, Employee, FollowupSchedule, LeadSource, LeadStage, PartnerProfile, PipelineStage, Product
from schemas import (
    ContactCreate,
    ContactListItem,
    ContactListResponse,
    ContactResponse,
    ContactType,
    ContactUpdate,
    LeadStageCreate,
    LeadStageResponse,
    PipelineStageItem,
)
from schemas.dealer_profile import DealerProfileResponse, DealerProfileUpdate
from schemas.enums import profession_key
import settings_catalog as catalog

router = APIRouter(
    prefix="/contacts", tags=["contacts"], dependencies=[Depends(get_current_user)]
)

# Roles that are never auto-assigned.
# Admins and managers oversee leads; they don't handle follow-ups themselves.
NON_ASSIGNABLE_ROLES = ("admin", "manager")


def _stage_response(stage: LeadStage) -> LeadStageResponse:
    """
    Convert a LeadStage DB row into a LeadStageResponse schema object.

    WHY NOT model_validate() ALONE?
        LeadStage stores only `stage_id` (a foreign key integer). The slug and
        human-readable name live in PipelineStage. This function reads the related
        PipelineStage via `stage.stage_type` and copies slug/name/contact_type
        onto the response object before returning it.

    INPUT:  a LeadStage ORM object (with stage_type relationship loaded or lazy-loaded)
    OUTPUT: a LeadStageResponse Pydantic object with slug and name filled in
    """
    st = stage.stage_type
    r = LeadStageResponse.model_validate(stage, from_attributes=True)
    if st is not None:
        r.stage_slug = st.slug
        r.stage_name = st.name
        r.contact_type = st.contact_type
    if stage.changed_by_employee:
        r.changed_by_name = stage.changed_by_employee.name
    return r


def _get_new_lead_stage(db: Session, contact_type: str) -> PipelineStage:
    """
    Fetch the 'new_lead' pipeline stage for the given contact type.

    Every new contact starts at the 'new_lead' stage. This function looks it up
    so the caller can attach a LeadStage row to the new contact.

    Raises 500 if the pipeline has not been seeded (should never happen in production,
    but a clear error is better than a confusing NoneType crash).

    INPUT:  db session, contact_type string ("consumer" / "partner" / "dealer")
    OUTPUT: PipelineStage ORM object for the 'new_lead' stage of that type
    """
    st = db.query(PipelineStage).filter(
        PipelineStage.slug == "new_lead",
        PipelineStage.contact_type == contact_type,
        PipelineStage.is_active == 1,
    ).first()
    if not st:
        raise HTTPException(status_code=500, detail="Pipeline not seeded: new_lead stage missing")
    return st


def _pick_random_assignee(db: Session) -> int:
    """
    Pick a random active, non-admin, non-manager employee to own a new lead.

    Used when the caller does not specify an assignee. The random selection
    distributes new leads across the team automatically.

    SQL equivalent:
        SELECT id FROM employees
        WHERE deleted_at IS NULL AND is_active = 1 AND role NOT IN ('admin','manager')
        ORDER BY RAND() LIMIT 1;

    INPUT:  db session
    OUTPUT: employee id (int)
    RAISES: 409 if no eligible employee exists
    """
    employee = (
        db.query(Employee)
        .filter(
            Employee.deleted_at.is_(None),
            Employee.is_active == 1,
            Employee.role.notin_(NON_ASSIGNABLE_ROLES),
        )
        .order_by(func.rand() if db.get_bind().dialect.name == "mysql" else func.random())
        .first()
    )
    if employee is None:
        raise HTTPException(
            status_code=409, detail="No eligible employee available to assign this lead"
        )
    return employee.id


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


def _check_location(source: LeadSource | None, latitude, longitude, creating: bool = False) -> None:
    """
    Enforce GPS coordinate rules based on the lead source.

    RULES:
        - Offline leads MUST have latitude + longitude when first created.
        - All other lead sources MUST NOT have any coordinates.

    WHY SEPARATE FOR CREATE VS UPDATE?
        On update, the caller might only be changing the name — we shouldn't
        suddenly demand GPS. The `creating=True` flag makes the "must have GPS"
        check only run on new offline contacts.

    INPUT:  the LeadSource ORM object (or None), latitude, longitude, creating flag
    RAISES: 400 if the rules are violated
    """
    if source is not None and source.slug == "offline":
        if creating and (latitude is None or longitude is None):
            raise HTTPException(
                status_code=400, detail="latitude and longitude are required for offline leads"
            )
    elif latitude is not None or longitude is not None:
        raise HTTPException(
            status_code=400, detail="latitude and longitude are only allowed for offline leads"
        )


def _check_type_profiles(contact_type: str, consumer_profile, partner_profile, dealer_profile) -> None:
    """
    Ensure the caller isn't sending a profile block for the wrong contact type.

    For example, sending `partner_profile` for a consumer contact is a mistake.
    This catches it early before any DB writes happen.

    INPUT:  the resolved contact_type string, and the three optional profile inputs
    RAISES: 400 if a profile block does not match the contact type
    """
    if consumer_profile is not None and contact_type != "consumer":
        raise HTTPException(status_code=400, detail="consumer_profile is only allowed for consumer contacts")
    if partner_profile is not None and contact_type != "partner":
        raise HTTPException(status_code=400, detail="partner_profile is only allowed for partner contacts")
    if dealer_profile is not None and contact_type != "dealer":
        raise HTTPException(status_code=400, detail="dealer_profile is only allowed for dealer contacts")


def _check_consumer_options(db: Session, profile, current=None) -> None:
    """
    Validate that consumer profile dropdown values exist in the Settings catalog.

    The admin can add or hide options in Settings > Lists & Options. This function
    checks that `house_stage` and `current_tank_type` are currently active options.

    EXISTING VALUE GRACE:
        If a record already has a value that was later hidden by the admin, editing
        OTHER fields on that record should not suddenly fail. The `current` parameter
        is the existing profile — its value is always allowed even if hidden.

    INPUT:  db session, the incoming profile data, optional current profile from DB
    RAISES: 400 if a value is not an active option in Settings
    """
    if profile is None:
        return
    for field, category in (("house_stage", catalog.HOUSE_STAGE), ("current_tank_type", catalog.TANK_TYPE)):
        value = getattr(profile, field)
        if value is not None:
            catalog.ensure_allowed(db, category, value, getattr(current, field, None))


def contact_filters(
    contact_type: ContactType | None = None,
    profession_type: str | None = Query(None, description="Partner profession, e.g. plumber"),
    district: str | None = None,
    search: str | None = Query(None, description="Matches name or phone"),
    stage_id: int | None = Query(None, description="Filter by pipeline stage (stage_types.id)"),
    lead_source_id: int | None = None,
    assigned_to: int | None = None,
    created_by: int | None = None,
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


def contact_query(db: Session, f: dict, entities, user: Employee):
    """
    Build a SQLAlchemy query for contacts matching the given filters.

    This is the core query used by the list endpoint, the export, and the count.
    It is kept in one place so all three always return the same data.

    HOW THE CURRENT STAGE JOIN WORKS:
        LeadStage rows are append-only (we never update them, only add new ones).
        To find the current stage, we:
            1. Find the MAX(id) per contact_id in a subquery called `latest`.
            2. Outer-join Contact → latest → LeadStage to get that one row.
        Using max(id) instead of max(changed_at) is safe because ids are always
        strictly increasing and avoids ambiguity if two stages share a timestamp.

    SQL equivalent (simplified):
        SELECT contact.*, latest_stage.*
        FROM contacts
        LEFT JOIN (
            SELECT contact_id, MAX(id) AS latest_row_id FROM lead_stages GROUP BY contact_id
        ) AS latest ON latest.contact_id = contacts.id
        LEFT JOIN lead_stages AS current ON current.id = latest.latest_row_id
        WHERE contacts.deleted_at IS NULL
          AND <all active filters>

    INPUT:
        db       — SQLAlchemy session
        f        — filter dict from contact_filters()
        entities — a lambda that receives the `current` stage alias and returns
                   the list of columns/models to SELECT. This lets callers ask for
                   (func.count(Contact.id),) for the count query, or
                   (Contact, current) for the full list.
        user     — the currently signed-in employee (for future row-level access control)

    OUTPUT: SQLAlchemy Query object (not yet executed; caller calls .all() or .scalar())
    """
    # Subquery: for each contact, find the id of its most recent LeadStage row.
    latest = (
        db.query(
            LeadStage.contact_id.label("contact_id"),
            func.max(LeadStage.id).label("latest_row_id"),
        )
        .group_by(LeadStage.contact_id)
        .subquery()
    )
    # `aliased` creates a second reference to LeadStage so SQLAlchemy doesn't
    # confuse it with other joins to the same table.
    current = aliased(LeadStage)
    q = (
        db.query(*entities(current))
        .select_from(Contact)
        .outerjoin(latest, latest.c.contact_id == Contact.id)
        .outerjoin(current, current.id == latest.c.latest_row_id)
        .filter(Contact.deleted_at.is_(None))  # Exclude soft-deleted contacts
    )

    # Apply each filter only if it was provided (avoids unnecessary WHERE clauses)
    if "contact_type" in f:
        q = q.filter(Contact.contact_type == f["contact_type"].value)
    if "profession_type" in f:
        q = q.join(PartnerProfile, PartnerProfile.contact_id == Contact.id).filter(
            # Legacy rows hold free text ("Plumber", "Interior Designer"), so normalise before comparing
            func.replace(func.lower(PartnerProfile.profession_type), " ", "_") == f["profession_type"]
        )
    if "district" in f:
        q = q.filter(Contact.district.ilike(f"%{f['district']}%"))
    if "search" in f:
        like = f"%{f['search'].strip()}%"
        q = q.filter(or_(Contact.name.ilike(like), Contact.phone.ilike(like)))
    if "stage_id" in f:
        q = q.filter(current.stage_id == f["stage_id"])
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
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /contacts — Paged list of contacts with filters and sort.

    HOW PAGINATION WORKS:
        `skip=0&limit=10` → first 10 rows
        `skip=10&limit=10` → next 10 rows
        The response always includes `total` so the UI can show page numbers.

    TWO QUERIES PER REQUEST:
        1. COUNT query — runs without offset/limit to get the total matching count.
        2. DATA query  — runs with offset/limit and joinedload to fetch the page.

    WHY joinedload?
        Without it, SQLAlchemy would run a separate SELECT for every relationship
        on every row (N+1 query problem). joinedload fetches related rows in one
        JOIN, which is much faster for a page of 10–100 contacts.

    STAGE NAME BATCH LOAD:
        The current stage for each contact stores only a `stage_id` integer.
        We collect all unique stage ids on the page, fetch their names in ONE query,
        then build a lookup dict — avoiding N extra queries for stage names.
    """
    # 1. Total count (ignores skip/limit so the UI knows the full result set size)
    total = contact_query(db, f, lambda cur: (func.count(Contact.id),), current_user).scalar() or 0

    # 2. Page data: fetch Contact + current LeadStage alias for each row
    rows = (
        contact_query(db, f, lambda cur: (Contact, cur), current_user)
        .options(
            joinedload(Contact.lead_source),
            joinedload(Contact.assigned_employee),
            joinedload(Contact.creator),
            joinedload(Contact.partner_profile),
            joinedload(Contact.consumer_profile),
            joinedload(Contact.dealer_profile),
        )
        .order_by(*CONTACT_ORDERINGS[sort])
        .offset(skip)
        .limit(limit)
        .all()
    )

    # 3. Batch-load stage names for all stage ids on this page (one query, not N)
    stage_ids = {s.stage_id for _, s in rows if s is not None}
    stage_meta: dict[int, tuple[str, str]] = {}
    if stage_ids:
        for st in db.query(PipelineStage).filter(PipelineStage.id.in_(stage_ids)).all():
            stage_meta[st.id] = (st.slug, st.name)

    # 4. Build the response list
    items = []
    for contact, current_stage in rows:
        pending = missing_fields(contact)
        lead_stage_val = None
        if current_stage is not None:
            slug, name = stage_meta.get(current_stage.stage_id, ("", ""))
            lead_stage_val = {
                "stage_id": current_stage.stage_id,
                "stage_slug": slug,
                "stage_name": name,
                "changed_at": current_stage.changed_at,
            }
        item = ContactListItem.model_validate(
            {
                "id": contact.id,
                "contact_type": contact.contact_type,
                "name": contact.name,
                "phone": contact.phone,
                "address": contact.address,
                "district": contact.district,
                # Type-specific headline fields — None when the type doesn't apply
                "profession_type": profession_key(contact.partner_profile.profession_type) if contact.partner_profile else None,
                "house_stage": contact.consumer_profile.house_stage if contact.consumer_profile else None,
                "owner_name": contact.dealer_profile.owner_name if contact.dealer_profile else None,
                "work_area": contact.partner_profile.work_area if contact.partner_profile else None,
                "latitude": contact.latitude,
                "longitude": contact.longitude,
                "lead_stage": lead_stage_val,
                "lead_source": contact.lead_source,
                "assigned_to": contact.assigned_employee,
                "created_by": contact.creator,
                "created_at": contact.created_at,
                "is_verified": not pending,
                "missing_fields": pending,
            },
            from_attributes=True,
        )
        items.append(item)

    return ContactListResponse(items=items, total=total, skip=skip, limit=limit)



@router.get("/partner-types", summary="List partner profession types")
def list_profession_types(db: Session = Depends(get_db)):
    """Active partner types from Settings > Lists & Options. The `value` is what the API stores."""
    return [{"value": i.value, "label": i.label} for i in catalog.list_items(db, catalog.PROFESSIONAL_TYPE) if i.is_active]


@router.get("/export", summary="Export contacts")
def export_contacts(
    format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
    f: dict = Depends(contact_filters),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Downloads the contacts matching the list filters as CSV, Excel or PDF (`format`).
    It uses the same filters as the list, so the export matches what is on screen.
    """
    total = contact_query(db, f, lambda cur: (func.count(Contact.id),), current_user).scalar() or 0
    limit = contact_export.MAX_ROWS[format]
    if total > limit:
        raise HTTPException(
            status_code=400,
            detail=f"{total} contacts match; a {format.upper()} export holds at most {limit}. Narrow the filters.",
        )
    rows = (
        contact_query(db, f, lambda cur: (Contact, cur), current_user)
        .options(
            joinedload(Contact.lead_source),
            joinedload(Contact.assigned_employee),
            joinedload(Contact.creator),
            joinedload(Contact.partner_profile),
            joinedload(Contact.consumer_profile),
            joinedload(Contact.dealer_profile),
        )
        .order_by(Contact.created_at.desc(), Contact.id.desc())
        .all()
    )
    export_stage_ids = {s.stage_id for _, s in rows if s is not None}
    export_stage_slugs: dict[int, str] = {}
    if export_stage_ids:
        for st in db.query(PipelineStage).filter(PipelineStage.id.in_(export_stage_ids)).all():
            export_stage_slugs[st.id] = st.slug
    table = contact_export.build_rows(
        [(c, export_stage_slugs.get(s.stage_id) if s else None, not missing_fields(c)) for c, s in rows]
    )
    if format == "csv":
        body = contact_export.to_csv(table)
    elif format == "xlsx":
        body = contact_export.to_xlsx(table)
    else:
        summary = ", ".join(f"{k.replace('_', ' ')}: {getattr(v, 'value', v)}" for k, v in f.items())
        body = contact_export.to_pdf(table, summary)
    filename = f"contacts_{date.today():%Y%m%d}.{format}"
    return Response(
        body,
        media_type=contact_export.MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/import/template", summary="Download import template")
def import_template():
    """A blank CSV with the headers the importer recognises and one example row."""
    return Response(
        contact_import.template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="contacts_import_template.csv"'},
    )


def _read_upload(file: UploadFile, mapping_json: str | None):
    """Rows, header row and column mapping for an uploaded sheet (auto-detected unless given)."""
    try:
        rows = contact_import.read_rows(file.filename or "", file.file.read(contact_import.MAX_BYTES + 1))
    except contact_import.ImportFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    header_row, detected = contact_import.detect_header(rows)
    if mapping_json:
        try:
            chosen = {int(col): fld for col, fld in json.loads(mapping_json).items() if fld in contact_import.FIELDS}
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="mapping must be a JSON object of column index to field")
    else:
        chosen = detected
    return rows, header_row, chosen


def _import_options(default_type: str, default_source_id: int | None, duplicates: str) -> contact_import.Options:
    if default_type not in ("consumer", "partner", "dealer"):
        raise HTTPException(status_code=400, detail="default_type must be consumer, partner or dealer")
    if duplicates not in ("skip", "update_blank"):
        raise HTTPException(status_code=400, detail="duplicates must be skip or update_blank")
    return contact_import.Options(default_type, default_source_id, duplicates)


@router.post("/import/analyze", summary="Check an import file")
def analyze_import(
    file: UploadFile = File(...),
    mapping: str | None = Form(None, description="JSON {column index: field}; auto-detected when omitted"),
    default_type: str = Form("consumer"),
    default_source_id: int | None = Form(None),
    duplicates: str = Form("skip"),
    db: Session = Depends(get_db),
    _: Employee = Depends(get_current_user),
):
    """Reads an uploaded sheet and reports the detected columns and every row that needs attention.
    Nothing is written.
    """
    rows, header_row, chosen = _read_upload(file, mapping)
    options = _import_options(default_type, default_source_id, duplicates)
    results = contact_import.analyze(db, rows, header_row, chosen, options)
    headers = rows[header_row]
    return {
        "header_row": header_row + 1,
        "columns": [
            {
                "index": i,
                "header": headers[i] if i < len(headers) else "",
                "field": chosen.get(i),
                "sample": [r[i] for r in rows[header_row + 1:header_row + 4] if i < len(r) and r[i]],
            }
            for i in range(max(len(headers), max(chosen, default=-1) + 1))
        ],
        "fields": contact_import.FIELDS,
        "missing_required": [f for f in ("name", "phone") if f not in chosen.values()],
        "summary": contact_import.summarize(results),
        "rows": [
            {"row": r.row, "status": r.status, "issues": r.issues, "raw": r.raw,
             "name": r.data.get("name"), "phone": r.data.get("phone")}
            for r in results if r.status != "ready"
        ],
    }


@router.post("/import/commit", summary="Import contacts from a file")
def commit_import(
    file: UploadFile = File(...),
    mapping: str = Form(...),
    default_type: str = Form("consumer"),
    default_source_id: int | None = Form(None),
    duplicates: str = Form("skip"),
    assigned_to: int | None = Form(None, description="Owner for every imported contact; random eligible employee when omitted"),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Writes the ready and warning rows of an uploaded sheet. Error rows are skipped.
    `duplicates` decides what happens to a row that matches an existing contact (skip it, or fill only its blank fields).
    """
    rows, header_row, chosen = _read_upload(file, mapping)
    if "name" not in chosen.values() or "phone" not in chosen.values():
        raise HTTPException(status_code=400, detail="Map the Name and Phone columns first")
    options = _import_options(default_type, default_source_id, duplicates)
    if default_source_id is not None and not db.get(LeadSource, default_source_id):
        raise HTTPException(status_code=400, detail="Lead source not found")

    if assigned_to is not None:
        if not db.query(Employee).filter(Employee.id == assigned_to, Employee.deleted_at.is_(None)).first():
            raise HTTPException(status_code=400, detail="Assigned employee not found")
        assignees = [assigned_to]
    else:
        assignees = [
            e.id for e in db.query(Employee).filter(
                Employee.deleted_at.is_(None), Employee.is_active == 1, Employee.role.notin_(NON_ASSIGNABLE_ROLES)
            )
        ]
        if not assignees:
            raise HTTPException(status_code=409, detail="No eligible employee available to assign these leads")

    results = contact_import.analyze(db, rows, header_row, chosen, options)
    return contact_import.run_import(db, results, options, assignees, current_user.id)


@router.get("/type-counts", summary="Count contacts per type")
def contact_type_counts(
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """A live count of the contacts per type (consumer, partner, dealer), for the tab badges."""
    q = db.query(Contact.contact_type, func.count(Contact.id)).filter(Contact.deleted_at.is_(None))
    rows = q.group_by(Contact.contact_type).all()
    counts = {"consumer": 0, "partner": 0, "dealer": 0}
    for kind, total in rows:
        counts[str(getattr(kind, "value", kind))] = total
    return {"all": sum(counts.values()), **counts}


@router.get("/{contact_id}", response_model=ContactResponse, summary="Get a contact")
def get_contact(
    contact_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /contacts/{id} — Full detail for one contact.

    HOW IT WORKS:
        1. `get_live_contact()` fetches the contact and raises 404 if it is
           deleted or not found — so the rest of the function can trust that
           `contact` is a valid, live row.
        2. `model_validate(..., from_attributes=True)` converts the SQLAlchemy
           object to a Pydantic response. Relationships already loaded (assigned_employee,
           lead_source, profiles) are included automatically.
        3. `referrals_count` requires a COUNT query that Pydantic cannot run,
           so we set it explicitly.
        4. `missing_fields()` returns the list of required fields still empty,
           from which `is_verified` is derived.
        5. The current stage is the lead_stage with the latest changed_at timestamp.
    """
    contact = get_live_contact(db, contact_id)

    response = ContactResponse.model_validate(contact, from_attributes=True)

    # How many contacts does this person refer? Requires a COUNT — set it explicitly.
    response.referrals_count = (
        db.query(func.count(Contact.id))
        .filter(Contact.referred_by_id == contact_id)
        .scalar() or 0
    )

    response.missing_fields = missing_fields(contact)
    response.is_verified = not response.missing_fields

    if contact.lead_stages:
        latest_stage = max(contact.lead_stages, key=lambda s: s.changed_at)
        response.current_stage = _stage_response(latest_stage)

    return response


@router.post("", response_model=ContactResponse, status_code=201, summary="Create a contact")
def create_contact(
    body: ContactCreate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
    platform: str = Depends(client_platform),
):
    """
    POST /contacts — Create a new contact (consumer, partner, or dealer).

    CREATION FLOW:
        1. Mobile-only validation: mobile app must always send address + district.
        2. Validate lead source, GPS rules, referrer, profile blocks, and phone uniqueness.
        3. Build the Contact ORM object.
        4. Attach the type-specific profile (consumer/partner/dealer) if provided.
        5. Attach an initial "new_lead" LeadStage row — every contact starts here.
        6. db.flush() writes the contact to get its auto-generated `id` before commit.
        7. If an assignee was chosen, write a ContactAssignment history row.
        8. db.commit() persists everything atomically (all or nothing).
        9. db.refresh(contact) reloads the contact from DB so relationships are populated.
       10. Build and return the response.

    WHY db.flush() BEFORE db.add(ContactAssignment)?
        ContactAssignment needs `contact.id`. flush() sends the INSERT to MySQL and
        gets the auto-increment id back, but the transaction isn't committed yet.
        If anything after this fails, the entire transaction rolls back cleanly.

    ASSIGNMENT BEHAVIOUR:
        - body.assigned_to provided → validate employee exists, assign to them.
        - body.assigned_to omitted  → contact is created UNASSIGNED (assigned_to = NULL).
          A manager reviews new leads and assigns them manually.
    """
    # Step 1: mobile app requires address and district
    if platform == "mobile":
        missing = [name for name in ("address", "district") if not getattr(body, name)]
        if missing:
            raise HTTPException(status_code=400, detail=f"{' and '.join(missing).capitalize()} required from the mobile app")

    # Step 2: validate all business rules before touching the DB
    source = db.query(LeadSource).filter(LeadSource.id == body.lead_source_id).first()
    if not source:
        raise HTTPException(status_code=400, detail="Lead source not found")
    _check_location(source, body.latitude, body.longitude, creating=True)
    _ensure_referrer(db, body.referred_by_id)
    _check_type_profiles(body.contact_type, body.consumer_profile, body.partner_profile, body.dealer_profile)
    if body.partner_profile is not None:
        catalog.ensure_allowed(db, catalog.PROFESSIONAL_TYPE, body.partner_profile.profession_type)
    _check_consumer_options(db, body.consumer_profile)
    if body.contact_type == "partner" and body.partner_profile is None:
        raise HTTPException(status_code=400, detail="partner_profile (with profession_type) is required for a partner")
    if body.contact_type == "dealer" and body.dealer_profile is None:
        raise HTTPException(status_code=400, detail="dealer_profile (with owner_name) is required for a dealer")
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
    data = body.model_dump(exclude={"consumer_profile", "partner_profile", "dealer_profile", "assigned_to", "interested_product_ids"})
    contact = Contact(**data, phone_key=phone_key(body.phone), assigned_to=assigned_to, created_by=current_user.id)

    # Step 4: attach type-specific profile in the same transaction
    if body.consumer_profile is not None:
        contact.consumer_profile = ConsumerProfile(**body.consumer_profile.model_dump(mode="json"))
    if body.partner_profile is not None:
        contact.partner_profile = PartnerProfile(**body.partner_profile.model_dump(mode="json"))
    if body.dealer_profile is not None:
        contact.dealer_profile = DealerProfile(**body.dealer_profile.model_dump(mode="json"))

    # Link any interested products (validated: must exist and not be deleted)
    if body.interested_product_ids:
        product_rows = db.query(Product).filter(
            Product.id.in_(body.interested_product_ids), Product.deleted_at.is_(None)
        ).all()
        if len(product_rows) != len(set(body.interested_product_ids)):
            raise HTTPException(status_code=400, detail="One or more selected products were not found")
        contact.interested_products = product_rows

    # Step 5: every contact enters the pipeline at the "new_lead" stage
    new_lead_type = _get_new_lead_stage(db, body.contact_type.value if body.contact_type else "consumer")
    contact.lead_stages.append(LeadStage(stage_id=new_lead_type.id, changed_by=current_user.id))

    # Steps 6–8: write to DB
    db.add(contact)
    db.flush()  # Get contact.id without committing yet
    if assigned_to is not None:
        db.add(ContactAssignment(contact_id=contact.id, assigned_to=assigned_to, changed_by=current_user.id))
    db.commit()
    db.refresh(contact)  # Reload so relationships (lead_source, assigned_employee, etc.) are populated

    # Steps 9–10: build response
    response = ContactResponse.model_validate(contact, from_attributes=True)
    response.referrals_count = 0  # Brand new contact cannot have referrals yet
    response.missing_fields = missing_fields(contact)
    response.is_verified = not response.missing_fields
    response.current_stage = _stage_response(contact.lead_stages[0])
    return response


@router.put("/{contact_id}", response_model=ContactResponse, summary="Update a contact")
def update_contact(
    contact_id: int,
    body: ContactUpdate,
    db: Session = Depends(get_db),
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

    ASSIGNMENT HISTORY:
        When assigned_to changes, we write a ContactAssignment row so managers can
        see the full ownership history. We track `assigned_from` (previous owner)
        and `assigned_to` (new owner).

    PROFILE UPSERT:
        - If the contact already has a profile, we merge the sent fields into it.
        - If the contact has no profile yet, we create one. In that case, required
          fields (profession_type for partners, owner_name for dealers) must be present.

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
    if platform == "mobile":
        for name in ("address", "district"):
            if name in fields and not getattr(body, name):
                raise HTTPException(status_code=400, detail=f"{name.capitalize()} required from the mobile app")

    # Re-validate GPS rules only if any location-related field was sent
    if fields & {"lead_source_id", "latitude", "longitude"}:
        source_id = body.lead_source_id if "lead_source_id" in fields else contact.lead_source_id
        source = (
            db.query(LeadSource).filter(LeadSource.id == source_id).first()
            if source_id is not None
            else None
        )
        if "lead_source_id" in fields and source_id is not None and source is None:
            raise HTTPException(status_code=400, detail="Lead source not found")
        _check_location(
            source,
            body.latitude if "latitude" in fields else contact.latitude,
            body.longitude if "longitude" in fields else contact.longitude,
        )

    # Use the new type if being changed, otherwise the existing type from the DB
    final_type = body.contact_type.value if body.contact_type is not None else contact.contact_type
    _check_type_profiles(final_type, body.consumer_profile, body.partner_profile, body.dealer_profile)
    _check_consumer_options(db, body.consumer_profile, contact.consumer_profile)

    if "phone" in fields and body.phone is not None:
        _ensure_phone_free(db, body.phone, current_user, exclude_id=contact.id)

    # Build a dict of only the fields to apply (excludes profiles and products, handled separately)
    update_data = body.model_dump(exclude_unset=True, exclude={"consumer_profile", "partner_profile", "dealer_profile", "interested_product_ids"})
    # Do not allow clearing required fields by sending null
    for key in ("contact_type", "name", "phone", "assigned_to"):
        if key in update_data and update_data[key] is None:
            del update_data[key]

    previous_owner = contact.assigned_to
    for field, value in update_data.items():
        setattr(contact, field, value)

    # Write assignment history if the owner changed
    if "assigned_to" in update_data and update_data["assigned_to"] != previous_owner:
        db.add(ContactAssignment(
            contact_id=contact.id, assigned_from=previous_owner,
            assigned_to=update_data["assigned_to"], changed_by=current_user.id,
        ))

    # If the phone was updated, recalculate the phone_key for duplicate detection
    if "phone" in update_data:
        contact.phone_key = phone_key(update_data["phone"])

    # --- Interested products: replace the full set when the caller explicitly sent the field ---
    # `interested_product_ids` in model_fields_set → the caller wants to change the list.
    # An empty list clears all products; a non-empty list replaces with the given ids.
    if "interested_product_ids" in fields and body.interested_product_ids is not None:
        ids = body.interested_product_ids
        if ids:
            product_rows = db.query(Product).filter(
                Product.id.in_(ids), Product.deleted_at.is_(None)
            ).all()
            if len(product_rows) != len(set(ids)):
                raise HTTPException(status_code=400, detail="One or more selected products were not found")
            contact.interested_products = product_rows
        else:
            contact.interested_products = []

    # --- Profile upsert logic ---

    if body.consumer_profile is not None:
        profile_data = body.consumer_profile.model_dump(exclude_unset=True, mode="json")
        if contact.consumer_profile is None:
            contact.consumer_profile = ConsumerProfile(**profile_data)
        else:
            for field, value in profile_data.items():
                setattr(contact.consumer_profile, field, value)

    if body.partner_profile is not None:
        profile_data = body.partner_profile.model_dump(exclude_unset=True, mode="json")
        if profile_data.get("profession_type"):
            # If the current profession_type was hidden by the admin, the record's existing
            # value stays valid. Pass it as `current` so ensure_allowed allows it through.
            current = profession_key(contact.partner_profile.profession_type) if contact.partner_profile else None
            catalog.ensure_allowed(db, catalog.PROFESSIONAL_TYPE, profile_data["profession_type"], current)
        if contact.partner_profile is None:
            if not profile_data.get("profession_type"):
                raise HTTPException(
                    status_code=400, detail="profession_type is required for a partner"
                )
            contact.partner_profile = PartnerProfile(**profile_data)
        else:
            for field, value in profile_data.items():
                if field == "profession_type" and value is None:
                    continue  # Never clear profession_type via a partial update
                setattr(contact.partner_profile, field, value)

    if body.dealer_profile is not None:
        profile_data = body.dealer_profile.model_dump(exclude_unset=True, mode="json")
        if contact.dealer_profile is None:
            if not profile_data.get("owner_name"):
                raise HTTPException(status_code=400, detail="owner_name is required for a dealer")
            contact.dealer_profile = DealerProfile(**profile_data)
        else:
            for field, value in profile_data.items():
                if field == "owner_name" and value is None:
                    continue  # Never clear owner_name via a partial update
                setattr(contact.dealer_profile, field, value)

    db.add(contact)
    db.commit()
    db.refresh(contact)

    response = ContactResponse.model_validate(contact, from_attributes=True)
    response.referrals_count = (
        db.query(func.count(Contact.id))
        .filter(Contact.referred_by_id == contact_id)
        .scalar() or 0
    )
    response.missing_fields = missing_fields(contact)
    response.is_verified = not response.missing_fields

    if contact.lead_stages:
        latest_stage = max(contact.lead_stages, key=lambda s: s.changed_at)
        response.current_stage = _stage_response(latest_stage)

    return response


@router.delete("/{contact_id}", status_code=204, summary="Delete a contact")
def delete_contact(
    contact_id: int,
    db: Session = Depends(get_db),
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

    contact.deleted_at = datetime.utcnow()
    db.add(contact)
    db.commit()




@router.get("/{contact_id}/stages", response_model=list[LeadStageResponse], summary="List stage history")
def get_contact_stages(
    contact_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /contacts/{id}/stages — Full pipeline history for a contact.

    Stage rows are append-only (we never update or delete them). The newest row
    is the contact's current stage. This endpoint returns all rows in
    descending order (newest first), so index 0 is always the current stage.

    joinedload(stage_type) and joinedload(changed_by_employee) are included so
    _stage_response() can read slug, name, and the changer's name without
    triggering additional lazy-load queries per row.
    """
    get_live_contact(db, contact_id)  # Raises 404 if contact is gone

    stages = (
        db.query(LeadStage)
        .options(joinedload(LeadStage.stage_type), joinedload(LeadStage.changed_by_employee))
        .filter(LeadStage.contact_id == contact_id)
        .order_by(LeadStage.changed_at.desc(), LeadStage.id.desc())
        .all()
    )

    return [_stage_response(s) for s in stages]


@router.post("/{contact_id}/stages", response_model=LeadStageResponse, status_code=201, summary="Move contact to a stage")
def add_contact_stage(
    contact_id: int,
    body: LeadStageCreate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    POST /contacts/{id}/stages — Move a contact to a new pipeline stage.

    HOW STAGE HISTORY WORKS:
        We never update or delete stage rows. Moving to a new stage means APPENDING
        a new LeadStage row. The contact's history is the full list; the current
        stage is always the most recent row.

    VALIDATION RULES:
        1. The employee recorded in `changed_by` must exist.
        2. The requested stage must be active.
        3. The stage must belong to this contact's pipeline type — EXCEPT "lost",
           which is a terminal stage that applies across all pipelines.
        4. Moving to "lost" requires a `loss_reason` (and must not have one otherwise).
        5. `awareness_note` is only valid when moving to the "aware" stage.
        6. A contact must have all required fields filled before leaving "new_lead".
           This is the verification gate — incomplete contacts stay at the first stage.
    """
    contact = get_live_contact(db, contact_id)

    employee = db.query(Employee).filter(Employee.id == body.changed_by).first()
    if not employee:
        raise HTTPException(status_code=400, detail="Employee not found")

    stage_type = db.query(PipelineStage).filter(
        PipelineStage.id == body.stage_id,
        PipelineStage.is_active == 1,
    ).first()
    if not stage_type:
        raise HTTPException(status_code=400, detail="Stage not found")

    # "lost" is the one stage that can be set from any pipeline type
    if stage_type.slug != "lost" and stage_type.contact_type != contact.contact_type:
        raise HTTPException(
            status_code=400,
            detail=f"Stage '{stage_type.slug}' is not part of the {contact.contact_type} pipeline",
        )

    if stage_type.slug == "lost" and body.loss_reason is None:
        raise HTTPException(status_code=400, detail="loss_reason is required when moving to Lost")
    if stage_type.slug != "lost" and body.loss_reason is not None:
        raise HTTPException(status_code=400, detail="loss_reason is only valid when moving to Lost")
    if stage_type.slug != "aware" and body.awareness_note is not None:
        raise HTTPException(status_code=400, detail="awareness_note is only valid when moving to Aware")

    if body.loss_reason is not None:
        catalog.ensure_allowed(db, catalog.LOSS_REASON, body.loss_reason)

    # Verification gate: a contact cannot advance past new_lead until all required
    # fields are filled. missing_fields() checks type-specific required fields.
    if stage_type.slug != "new_lead":
        pending = missing_fields(contact)
        if pending:
            raise HTTPException(status_code=409, detail={"missing_fields": pending, "message": "Complete the contact's details before advancing the stage"})

    new_stage = LeadStage(
        contact_id=contact_id,
        stage_id=body.stage_id,
        changed_by=body.changed_by,
        loss_reason=body.loss_reason,
        loss_notes=body.loss_notes,
        awareness_note=body.awareness_note,
    )
    db.add(new_stage)
    db.commit()
    db.refresh(new_stage)

    return _stage_response(new_stage)
