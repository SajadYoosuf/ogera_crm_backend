from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, aliased, joinedload
import json

import contact_export
import contact_import
from auth import get_current_user, require_roles
from contact_access import get_visible_contact, has_full_access, visible_to
from database import get_db
from phone_utils import phone_key
from request_context import client_platform
from verification import missing_fields, verified_clause
from models import Contact, ConsumerProfile, DealerProfile, Employee, LeadSource, LeadStage, PartnerProfile, StageType
from schemas import (
    ContactCreate,
    ContactListItem,
    ContactListResponse,
    ContactMapPin,
    ContactMapResponse,
    ContactResponse,
    ContactType,
    ContactUpdate,
    LeadStageCreate,
    LeadStageResponse,
    StageTypeItem,
)
from schemas.dealer_profile import DealerProfileResponse, DealerProfileUpdate
from schemas.enums import HouseStage, ProfessionType, TankType, profession_key
import settings_catalog as catalog

router = APIRouter(
    prefix="/contacts", tags=["contacts"], dependencies=[Depends(get_current_user)]
)

# Roles that never receive auto-assigned leads
NON_ASSIGNABLE_ROLES = ("admin", "manager", "support")
# Roles that may import contacts
MANAGER_ROLES = ("admin", "manager")


def _stage_response(stage: LeadStage) -> LeadStageResponse:
    """Build a LeadStageResponse including slug/name/type from the joined StageType (lazy-loaded ok)."""
    st = stage.stage_type
    r = LeadStageResponse.model_validate(stage, from_attributes=True)
    if st is not None:
        r.stage_slug = st.slug
        r.stage_name = st.name
        r.contact_type = st.contact_type
        r.is_terminal = bool(st.is_terminal)
    return r


def _get_new_lead_stage(db: Session, contact_type: str) -> StageType:
    """Return the 'new_lead' StageType row for the given contact_type."""
    st = db.query(StageType).filter(
        StageType.slug == "new_lead",
        StageType.contact_type == contact_type,
        StageType.is_active == 1,
    ).first()
    if not st:
        raise HTTPException(status_code=500, detail="Pipeline not seeded: new_lead stage missing")
    return st


def _pick_random_assignee(db: Session) -> int:
    """Random active employee who is not an admin, manager or support."""
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
    """One phone number belongs to one live contact, whatever its type. Compared on the last 10 digits.
    The message names the other contact only for admins and managers, so a restricted user cannot probe contacts."""
    q = db.query(Contact.id).filter(Contact.phone_key == phone_key(phone), Contact.deleted_at.is_(None))
    if exclude_id is not None:
        q = q.filter(Contact.id != exclude_id)
    hit = q.first()
    if hit is not None:
        detail = "A contact with this phone number already exists"
        if has_full_access(user):
            detail += f" (contact {hit.id})"
        raise HTTPException(status_code=409, detail=detail)


def _ensure_referrer(db: Session, referred_by_id: int | None, contact_id: int | None = None):
    if referred_by_id is None:
        return
    if referred_by_id == contact_id:
        raise HTTPException(status_code=400, detail="A contact cannot refer itself")
    referrer = db.query(Contact).filter(Contact.id == referred_by_id).first()
    if not referrer or referrer.deleted_at:
        raise HTTPException(status_code=400, detail="Referring contact not found")


def _check_location(source: LeadSource | None, latitude, longitude, creating: bool = False) -> None:
    """Offline (field visit) leads must come with a location when they are created; it is not allowed on other sources."""
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
    """Each type details block is only allowed on its own contact type."""
    if consumer_profile is not None and contact_type != "consumer":
        raise HTTPException(status_code=400, detail="consumer_profile is only allowed for consumer contacts")
    if partner_profile is not None and contact_type != "partner":
        raise HTTPException(status_code=400, detail="partner_profile is only allowed for partner contacts")
    if dealer_profile is not None and contact_type != "dealer":
        raise HTTPException(status_code=400, detail="dealer_profile is only allowed for dealer contacts")


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
    """Filters shared by the contact list and the export, so the two can never disagree."""
    params = dict(locals())
    return {k: v for k, v in params.items() if v is not None}


def contact_query(db: Session, f: dict, entities, user: Employee):
    """Contacts matching the filters and visible to the user. `entities(current_stage)` picks what to select."""
    # Latest stage per contact. Stage rows are append-only, so max(id) is the newest row.
    latest = (
        db.query(
            LeadStage.contact_id.label("contact_id"),
            func.max(LeadStage.id).label("latest_row_id"),
        )
        .group_by(LeadStage.contact_id)
        .subquery()
    )
    current = aliased(LeadStage)
    q = (
        db.query(*entities(current))
        .select_from(Contact)
        .outerjoin(latest, latest.c.contact_id == Contact.id)
        .outerjoin(current, current.id == latest.c.latest_row_id)
        .filter(Contact.deleted_at.is_(None))  # Exclude soft-deleted
    )
    scope = visible_to(user)
    if scope is not None:  # staff see only their own creations and assignments
        q = q.filter(scope)
    if "contact_type" in f:
        q = q.filter(Contact.contact_type == f["contact_type"].value)
    if "profession_type" in f:
        q = q.join(PartnerProfile, PartnerProfile.contact_id == Contact.id).filter(
            # Legacy rows hold free text ("Plumber", "Interior Designer"), so compare normalised
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
    if "created_from" in f:
        q = q.filter(Contact.created_at >= f["created_from"])
    if "created_to" in f:
        q = q.filter(Contact.created_at < f["created_to"] + timedelta(days=1))
    return q


# Sort orders offered by the Contacts "Sort by" control. The id tie-break keeps paging stable.
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
    """Paged list of contacts of every type, each with its current stage, lead source, owner and verification status.
    Filter by type, stage, district, owner, creator, verified state, creation dates and search text. `sort` sets the order.
    Admins and managers see every contact. Everyone else sees only contacts they created or are assigned to.
    """
    total = contact_query(db, f, lambda cur: (func.count(Contact.id),), current_user).scalar() or 0
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

    # Batch-load stage names for all stage ids in this page so each row has slug + name
    stage_ids = {s.stage_id for _, s in rows if s is not None}
    stage_meta: dict[int, tuple[str, str]] = {}
    if stage_ids:
        for st in db.query(StageType).filter(StageType.id.in_(stage_ids)).all():
            stage_meta[st.id] = (st.slug, st.name)

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


@router.get("/map", response_model=ContactMapResponse, summary="Contacts for the map")
def contact_map_pins(
    limit: int = Query(2000, ge=1, le=5000),
    f: dict = Depends(contact_filters),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Contacts that have a saved location, trimmed to what the mobile map needs. Takes the same filters as the list and the same visibility."""
    rows = (
        contact_query(db, f, lambda cur: (Contact, cur), current_user)
        .filter(Contact.latitude.is_not(None), Contact.longitude.is_not(None))
        .order_by(Contact.created_at.desc(), Contact.id.desc())
        .limit(limit)
        .all()
    )
    # Batch-load stage slugs for map pin stage indicators
    map_stage_ids = {s.stage_id for _, s in rows if s is not None}
    map_stage_slugs: dict[int, str] = {}
    if map_stage_ids:
        for st in db.query(StageType).filter(StageType.id.in_(map_stage_ids)).all():
            map_stage_slugs[st.id] = st.slug

    items = [
        ContactMapPin(
            id=c.id,
            contact_type=c.contact_type,
            name=c.name,
            phone=c.phone,
            address=c.address,
            latitude=float(c.latitude),
            longitude=float(c.longitude),
            stage_slug=map_stage_slugs.get(stage.stage_id) if stage else None,
            house_stage=c.consumer_profile.house_stage if c.consumer_profile else None,
        )
        for c, stage in rows
    ]
    return ContactMapResponse(items=items, total=len(items))


@router.get("/stage-types", response_model=list[StageTypeItem], summary="List pipeline stages")
def list_stage_types(
    contact_type: ContactType | None = Query(None, description="Filter stages to one pipeline (consumer, partner or dealer)"),
    db: Session = Depends(get_db),
):
    """Pipeline stages in board order. Pass contact_type to get only the stages for that pipeline."""
    q = db.query(StageType).filter(StageType.is_active == 1)
    if contact_type is not None:
        q = q.filter(StageType.contact_type == contact_type.value)
    rows = q.order_by(StageType.order, StageType.id).all()
    return [
        StageTypeItem(
            id=r.id,
            slug=r.slug,
            label=r.name,
            order_index=i,
            contact_type=r.contact_type,
            is_terminal=bool(r.is_terminal),
        )
        for i, r in enumerate(rows)
    ]


@router.get("/partner-types", summary="List partner profession types")
def list_profession_types():
    """Partner profession options. Fixed in code: the `value` is what the API stores."""
    return [{"value": p.value, "label": p.value.replace("_", " ").title()} for p in ProfessionType]


@router.get("/house-stages", summary="List house stages")
def list_house_stages():
    """Consumer house stages. Fixed in code, not in Settings."""
    return [{"value": s.value, "label": s.value.replace("_", " ").title()} for s in HouseStage]


@router.get("/tank-types", summary="List tank types")
def list_tank_types():
    """Consumer tank types. Fixed in code, not in Settings."""
    return [{"value": t.value, "label": t.value.replace("_", " ").title()} for t in TankType]


@router.get("/export", summary="Export contacts")
def export_contacts(
    format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
    f: dict = Depends(contact_filters),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Downloads the contacts matching the list filters as CSV, Excel or PDF (`format`).
    It uses the same filters and the same visibility as the list, so the export matches what is on screen.
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
        for st in db.query(StageType).filter(StageType.id.in_(export_stage_ids)).all():
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
    _: Employee = Depends(require_roles("admin", "manager")),
):
    """Reads an uploaded sheet and reports the detected columns and every row that needs attention.
    Nothing is written. Admin and manager only.
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
    current_user: Employee = Depends(require_roles("admin", "manager")),
):
    """Writes the ready and warning rows of an uploaded sheet. Error rows are skipped.
    `duplicates` decides what happens to a row that matches an existing contact (skip it, or fill only its blank fields).
    Admin and manager only.
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
    """A live count of the contacts this user can see, per type (consumer, partner, dealer), for the tab badges."""
    q = db.query(Contact.contact_type, func.count(Contact.id)).filter(Contact.deleted_at.is_(None))
    scope = visible_to(current_user)
    if scope is not None:
        q = q.filter(scope)
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
    """One contact with its current stage, referral count and verification status (`is_verified`, `missing_fields`).
    Returns 404 if the id is unknown, the contact is deleted, or the user is not allowed to see it.
    """
    contact = get_visible_contact(db, current_user, contact_id)

    response = ContactResponse.model_validate(contact, from_attributes=True)

    # Populate referrals_count
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
    """Creates a consumer, partner or dealer contact. The signed-in user is recorded as the creator.

    Always required: name, phone and lead source. Mobile requests (`X-Client: mobile`) also need address and district.
    A consumer, partner or dealer needs its own details block: a partner needs `partner_profile` (with profession_type),
    a dealer needs `dealer_profile` (with owner_name). Consumer details are optional.
    If `assigned_to` is omitted, an owner is picked automatically from the staff who take leads.
    Offline leads need latitude and longitude. Returns 400 for an unknown source, owner, referrer or a details block on the wrong type.
    Returns 409 when the phone number already belongs to another contact.
    """
    if platform == "mobile":
        missing = [name for name in ("address", "district") if not getattr(body, name)]
        if missing:
            raise HTTPException(status_code=400, detail=f"{' and '.join(missing).capitalize()} required from the mobile app")

    source = db.query(LeadSource).filter(LeadSource.id == body.lead_source_id).first()
    if not source:
        raise HTTPException(status_code=400, detail="Lead source not found")
    _check_location(source, body.latitude, body.longitude, creating=True)
    _ensure_referrer(db, body.referred_by_id)
    _check_type_profiles(body.contact_type, body.consumer_profile, body.partner_profile, body.dealer_profile)
    if body.contact_type == "partner" and body.partner_profile is None:
        raise HTTPException(status_code=400, detail="partner_profile (with profession_type) is required for a partner")
    if body.contact_type == "dealer" and body.dealer_profile is None:
        raise HTTPException(status_code=400, detail="dealer_profile (with owner_name) is required for a dealer")
    _ensure_phone_free(db, body.phone, current_user)

    if body.assigned_to is None:
        assigned_to = _pick_random_assignee(db)
    else:
        if not db.query(Employee).filter(Employee.id == body.assigned_to).first():
            raise HTTPException(status_code=400, detail="Assigned employee not found")
        assigned_to = body.assigned_to

    data = body.model_dump(exclude={"consumer_profile", "partner_profile", "dealer_profile", "assigned_to"})
    contact = Contact(**data, phone_key=phone_key(body.phone), assigned_to=assigned_to, created_by=current_user.id)
    if body.consumer_profile is not None:
        contact.consumer_profile = ConsumerProfile(**body.consumer_profile.model_dump(mode="json"))
    if body.partner_profile is not None:
        contact.partner_profile = PartnerProfile(**body.partner_profile.model_dump(mode="json"))
    if body.dealer_profile is not None:
        contact.dealer_profile = DealerProfile(**body.dealer_profile.model_dump(mode="json"))
    new_lead_type = _get_new_lead_stage(db, body.contact_type.value if body.contact_type else "consumer")
    contact.lead_stages.append(LeadStage(stage_id=new_lead_type.id, changed_by=current_user.id))
    db.add(contact)
    db.commit()
    db.refresh(contact)

    response = ContactResponse.model_validate(contact, from_attributes=True)
    response.referrals_count = 0
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
    """Changes a contact's details. Only the fields sent are changed.
    The same rules as create apply: details must match the contact type, offline leads need a location, the referrer must exist and cannot be the contact itself.
    Address and district cannot be cleared, and a changed phone number must not belong to another contact.
    """
    contact = get_visible_contact(db, current_user, contact_id)

    if body.assigned_to is not None:
        if not db.query(Employee).filter(Employee.id == body.assigned_to).first():
            raise HTTPException(status_code=400, detail="Assigned employee not found")

    _ensure_referrer(db, body.referred_by_id, contact_id)

    fields = body.model_fields_set
    if platform == "mobile":
        for name in ("address", "district"):
            if name in fields and not getattr(body, name):
                raise HTTPException(status_code=400, detail=f"{name.capitalize()} required from the mobile app")

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

    final_type = body.contact_type.value if body.contact_type is not None else contact.contact_type
    _check_type_profiles(final_type, body.consumer_profile, body.partner_profile, body.dealer_profile)

    if "phone" in fields and body.phone is not None:
        _ensure_phone_free(db, body.phone, current_user, exclude_id=contact.id)

    update_data = body.model_dump(exclude_unset=True, exclude={"consumer_profile", "partner_profile", "dealer_profile"})
    # Required columns can't be cleared
    for key in ("contact_type", "name", "phone", "assigned_to"):
        if key in update_data and update_data[key] is None:
            del update_data[key]
    for field, value in update_data.items():
        setattr(contact, field, value)
    if "phone" in update_data:
        contact.phone_key = phone_key(update_data["phone"])

    if body.consumer_profile is not None:
        profile_data = body.consumer_profile.model_dump(exclude_unset=True, mode="json")
        if contact.consumer_profile is None:
            contact.consumer_profile = ConsumerProfile(**profile_data)
        else:
            for field, value in profile_data.items():
                setattr(contact.consumer_profile, field, value)

    if body.partner_profile is not None:
        profile_data = body.partner_profile.model_dump(exclude_unset=True, mode="json")
        if contact.partner_profile is None:
            if not profile_data.get("profession_type"):
                raise HTTPException(
                    status_code=400, detail="profession_type is required for a partner"
                )
            contact.partner_profile = PartnerProfile(**profile_data)
        else:
            for field, value in profile_data.items():
                if field == "profession_type" and value is None:
                    continue
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
                    continue
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
    """Soft delete. The contact disappears from lists, but its row and history are kept. Returns 404 if it is gone or not visible to you."""
    contact = get_visible_contact(db, current_user, contact_id)

    contact.deleted_at = datetime.utcnow()
    db.add(contact)
    db.commit()




@router.get("/{contact_id}/stages", response_model=list[LeadStageResponse], summary="List stage history")
def get_contact_stages(
    contact_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Every stage a contact has been in, ordered by the time it was set. The newest entry is the current stage."""
    get_visible_contact(db, current_user, contact_id)

    stages = (
        db.query(LeadStage)
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
    """Adds a new stage to a contact's history and returns it with status 201.
    The stage must belong to the contact's pipeline (stage_types.contact_type must match).
    A loss reason must be configured in Settings. The signed-in user and the time are recorded.
    """
    contact = get_visible_contact(db, current_user, contact_id)

    employee = db.query(Employee).filter(Employee.id == body.changed_by).first()
    if not employee:
        raise HTTPException(status_code=400, detail="Employee not found")

    # Look up the stage being requested
    stage_type = db.query(StageType).filter(
        StageType.id == body.stage_id,
        StageType.is_active == 1,
    ).first()
    if not stage_type:
        raise HTTPException(status_code=400, detail="Stage not found")

    # The stage must belong to this contact's pipeline (lost is allowed from any pipeline)
    if stage_type.slug != "lost" and stage_type.contact_type != contact.contact_type:
        raise HTTPException(
            status_code=400,
            detail=f"Stage '{stage_type.slug}' is not part of the {contact.contact_type} pipeline",
        )

    # A lead can only leave new_lead once its required details are complete
    if stage_type.slug not in ("new_lead", "lost"):
        latest = (
            db.query(LeadStage)
            .filter(LeadStage.contact_id == contact_id)
            .order_by(LeadStage.id.desc())
            .first()
        )
        latest_slug = latest.stage_type.slug if latest and latest.stage_type else "new_lead"
        pending = missing_fields(contact)
        if latest_slug == "new_lead" and pending:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Complete the lead's details before moving it to the next stage",
                    "missing_fields": pending,
                },
            )

    # loss_reason is required when moving to 'lost'
    if stage_type.slug == "lost" and body.loss_reason is None:
        raise HTTPException(status_code=400, detail="loss_reason is required when moving to Lost")
    if stage_type.slug != "lost" and body.loss_reason is not None:
        raise HTTPException(status_code=400, detail="loss_reason is only valid when moving to Lost")
    if stage_type.slug != "aware" and body.awareness_note is not None:
        raise HTTPException(status_code=400, detail="awareness_note is only valid when moving to Aware")

    if body.loss_reason is not None:
        catalog.ensure_allowed(db, catalog.LOSS_REASON, body.loss_reason)

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
