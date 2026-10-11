from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, aliased, joinedload
import json

import contact_export
import contact_import
from auth import get_current_user, require_roles
from database import get_db
from verification import missing_fields, verified_clause
from models import Contact, ConsumerProfile, Employee, LeadSource, LeadStage, PartnerProfile
from schemas import (
    ContactCreate,
    ContactListItem,
    ContactListResponse,
    ContactResponse,
    ContactType,
    ProfessionType,
    StageEnum,
    ContactUpdate,
    ConsumerProfileResponse,
    ConsumerProfileUpdate,
    LeadStageCreate,
    LeadStageResponse,
)

router = APIRouter(
    prefix="/contacts", tags=["contacts"], dependencies=[Depends(get_current_user)]
)

# Roles that never receive auto-assigned leads
NON_ASSIGNABLE_ROLES = ("admin", "manager", "support")
# Roles that may import contacts and export everyone's contacts
MANAGER_ROLES = ("admin", "manager")


def _pick_random_assignee(db: Session) -> int:
    """Random active employee who is not an admin, manager or support."""
    employee = (
        db.query(Employee)
        .filter(
            Employee.deleted_at.is_(None),
            Employee.is_active == 1,
            Employee.role.notin_(NON_ASSIGNABLE_ROLES),
        )
        .order_by(func.rand())
        .first()
    )
    if employee is None:
        raise HTTPException(
            status_code=409, detail="No eligible employee available to assign this lead"
        )
    return employee.id


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


def contact_filters(
    contact_type: ContactType | None = None,
    profession_type: ProfessionType | None = Query(None, description="Partner profession, e.g. plumber"),
    district: str | None = None,
    search: str | None = Query(None, description="Matches name or phone"),
    stage: StageEnum | None = None,
    lead_source_id: int | None = None,
    assigned_to: int | None = None,
    created_by: int | None = None,
    verified: bool | None = Query(None, description="true = only verified, false = only unverified"),
    created_from: date | None = Query(None, description="Created on or after this date"),
    created_to: date | None = Query(None, description="Created on or before this date"),
) -> dict:
    """Filters shared by the contact list and the export, so the two can never disagree."""
    params = dict(locals())
    return {k: v for k, v in params.items() if v is not None}


def contact_query(db: Session, f: dict, entities):
    """Contacts matching the filters. `entities(current_stage)` picks what to select."""
    # Latest stage per contact. Stage rows are append-only, so max(id) is the newest.
    latest = (
        db.query(
            LeadStage.contact_id.label("contact_id"),
            func.max(LeadStage.id).label("stage_id"),
        )
        .group_by(LeadStage.contact_id)
        .subquery()
    )
    current = aliased(LeadStage)
    q = (
        db.query(*entities(current))
        .select_from(Contact)
        .outerjoin(latest, latest.c.contact_id == Contact.id)
        .outerjoin(current, current.id == latest.c.stage_id)
        .filter(Contact.deleted_at.is_(None))  # Exclude soft-deleted
    )
    if "contact_type" in f:
        q = q.filter(Contact.contact_type == f["contact_type"].value)
    if "profession_type" in f:
        q = q.join(PartnerProfile, PartnerProfile.contact_id == Contact.id).filter(
            # Legacy rows hold free text ("Plumber", "Interior Designer"), so compare normalised
            func.replace(func.lower(PartnerProfile.profession_type), " ", "_") == f["profession_type"].value
        )
    if "district" in f:
        q = q.filter(Contact.district.ilike(f"%{f['district']}%"))
    if "search" in f:
        like = f"%{f['search'].strip()}%"
        q = q.filter(or_(Contact.name.ilike(like), Contact.phone.ilike(like)))
    if "stage" in f:
        q = q.filter(current.stage == f["stage"].value)
    if "lead_source_id" in f:
        q = q.filter(Contact.lead_source_id == f["lead_source_id"])
    if "assigned_to" in f:
        q = q.filter(Contact.assigned_to == f["assigned_to"])
    if "created_by" in f:
        q = q.filter(Contact.created_by == f["created_by"])
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


@router.get("", response_model=ContactListResponse)
def list_contacts(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    sort: Literal["newest", "oldest", "name_asc", "name_desc"] = Query("newest"),
    f: dict = Depends(contact_filters),
    db: Session = Depends(get_db),
):
    """List contacts (any type) with current stage, lead source and owner."""
    total = contact_query(db, f, lambda cur: (func.count(Contact.id),)).scalar() or 0
    rows = (
        contact_query(db, f, lambda cur: (Contact, cur))
        .options(
            joinedload(Contact.lead_source),
            joinedload(Contact.assigned_employee),
            joinedload(Contact.creator),
            joinedload(Contact.partner_profile),
            joinedload(Contact.consumer_profile),
        )
        .order_by(*CONTACT_ORDERINGS[sort])
        .offset(skip)
        .limit(limit)
        .all()
    )

    items = []
    for contact, current_stage in rows:
        pending = missing_fields(contact)
        item = ContactListItem.model_validate(
            {
                "id": contact.id,
                "contact_type": contact.contact_type,
                "name": contact.name,
                "phone": contact.phone,
                "address": contact.address,
                "district": contact.district,
                "profession_type": ProfessionType.normalise(contact.partner_profile.profession_type) if contact.partner_profile else None,
                "lead_stage": current_stage,
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


@router.get("/profession-types")
def list_profession_types():
    """Partner profession options for the partner-type picker (value is what the API stores)."""
    return [
        {"value": p.value, "label": p.value.replace("_", " ").title()}
        for p in ProfessionType
        if p is not ProfessionType.other
    ]


@router.get("/export")
def export_contacts(
    format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
    f: dict = Depends(contact_filters),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Download the contacts matching the list filters as CSV, Excel or PDF."""
    if current_user.role not in MANAGER_ROLES:
        f["assigned_to"] = current_user.id  # everyone else exports only their own contacts

    total = contact_query(db, f, lambda cur: (func.count(Contact.id),)).scalar() or 0
    limit = contact_export.MAX_ROWS[format]
    if total > limit:
        raise HTTPException(
            status_code=400,
            detail=f"{total} contacts match; a {format.upper()} export holds at most {limit}. Narrow the filters.",
        )
    rows = (
        contact_query(db, f, lambda cur: (Contact, cur))
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
    table = contact_export.build_rows([(c, stage, not missing_fields(c)) for c, stage in rows])
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


@router.get("/import/template")
def import_template():
    """Blank CSV with the headers the importer recognises and one example row."""
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


@router.post("/import/analyze")
def analyze_import(
    file: UploadFile = File(...),
    mapping: str | None = Form(None, description="JSON {column index: field}; auto-detected when omitted"),
    default_type: str = Form("consumer"),
    default_source_id: int | None = Form(None),
    duplicates: str = Form("skip"),
    db: Session = Depends(get_db),
    _: Employee = Depends(require_roles("admin", "manager")),
):
    """Check a sheet without writing anything: detected columns, and every row that needs attention."""
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


@router.post("/import/commit")
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
    """Import the ready and warning rows of a sheet. Errors are skipped; duplicates follow `duplicates`."""
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


@router.get("/{contact_id}", response_model=ContactResponse)
def get_contact(contact_id: int, db: Session = Depends(get_db)):
    """Get a single contact by ID."""
    contact = db.query(Contact).filter(Contact.id == contact_id).first()

    if not contact or contact.deleted_at:
        raise HTTPException(status_code=404, detail="Contact not found")

    response = ContactResponse.model_validate(contact, from_attributes=True)

    # Populate referrals_count
    response.referrals_count = (
        db.query(func.count(Contact.id))
        .filter(Contact.referred_by_id == contact_id)
        .scalar() or 0
    )

    response.missing_fields = missing_fields(contact)
    response.is_verified = not response.missing_fields

    # Populate current_stage
    if contact.lead_stages:
        latest_stage = max(contact.lead_stages, key=lambda s: s.changed_at)
        response.current_stage = LeadStageResponse.model_validate(
            latest_stage, from_attributes=True
        )

    return response


@router.post("", response_model=ContactResponse, status_code=201)
def create_contact(
    body: ContactCreate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Create a new contact. Owner is auto-assigned if omitted; creator is the signed-in user."""
    source = None
    if body.lead_source_id is not None:
        source = db.query(LeadSource).filter(LeadSource.id == body.lead_source_id).first()
        if not source:
            raise HTTPException(status_code=400, detail="Lead source not found")
    _check_location(source, body.latitude, body.longitude, creating=True)
    _ensure_referrer(db, body.referred_by_id)

    if body.consumer_profile is not None and body.contact_type != "consumer":
        raise HTTPException(
            status_code=400, detail="consumer_profile is only allowed for consumer contacts"
        )

    if body.assigned_to is None:
        assigned_to = _pick_random_assignee(db)
    else:
        if not db.query(Employee).filter(Employee.id == body.assigned_to).first():
            raise HTTPException(status_code=400, detail="Assigned employee not found")
        assigned_to = body.assigned_to

    if body.partner_profile is not None and body.contact_type != "partner":
        raise HTTPException(
            status_code=400, detail="partner_profile is only allowed for partner contacts"
        )

    data = body.model_dump(exclude={"consumer_profile", "partner_profile", "assigned_to"})
    contact = Contact(**data, assigned_to=assigned_to, created_by=current_user.id)
    if body.consumer_profile is not None:
        contact.consumer_profile = ConsumerProfile(**body.consumer_profile.model_dump())
    if body.partner_profile is not None:
        contact.partner_profile = PartnerProfile(**body.partner_profile.model_dump(mode="json"))
    # Every contact starts as a New Lead; Aware is only reached by moving the lead there
    contact.lead_stages.append(LeadStage(stage="new_lead", changed_by=current_user.id))
    db.add(contact)
    db.commit()
    db.refresh(contact)

    response = ContactResponse.model_validate(contact, from_attributes=True)
    response.referrals_count = 0
    response.current_stage = LeadStageResponse.model_validate(
        contact.lead_stages[0], from_attributes=True
    )
    return response


@router.put("/{contact_id}", response_model=ContactResponse)
def update_contact(
    contact_id: int,
    body: ContactUpdate,
    db: Session = Depends(get_db),
):
    """Update a contact."""
    contact = db.query(Contact).filter(Contact.id == contact_id).first()

    if not contact or contact.deleted_at:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Validate assigned_to if provided
    if body.assigned_to is not None:
        from models import Employee

        employee = db.query(Employee).filter(Employee.id == body.assigned_to).first()
        if not employee:
            raise HTTPException(status_code=400, detail="Assigned employee not found")

    _ensure_referrer(db, body.referred_by_id, contact_id)

    fields = body.model_fields_set
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

    update_data = body.model_dump(exclude_unset=True, exclude={"consumer_profile", "partner_profile"})
    # Required columns can't be cleared
    for key in ("contact_type", "name", "phone", "district", "address", "assigned_to"):
        if key in update_data and update_data[key] is None:
            del update_data[key]
    for field, value in update_data.items():
        setattr(contact, field, value)

    if body.consumer_profile is not None:
        if contact.contact_type != "consumer":
            raise HTTPException(
                status_code=400, detail="consumer_profile is only allowed for consumer contacts"
            )
        profile_data = body.consumer_profile.model_dump(exclude_unset=True)
        if contact.consumer_profile is None:
            contact.consumer_profile = ConsumerProfile(**profile_data)
        else:
            for field, value in profile_data.items():
                setattr(contact.consumer_profile, field, value)

    if body.partner_profile is not None:
        if contact.contact_type != "partner":
            raise HTTPException(
                status_code=400, detail="partner_profile is only allowed for partner contacts"
            )
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

    db.add(contact)
    db.commit()
    db.refresh(contact)

    response = ContactResponse.model_validate(contact, from_attributes=True)
    response.referrals_count = (
        db.query(func.count(Contact.id))
        .filter(Contact.referred_by_id == contact_id)
        .scalar() or 0
    )

    if contact.lead_stages:
        latest_stage = max(contact.lead_stages, key=lambda s: s.changed_at)
        response.current_stage = LeadStageResponse.model_validate(
            latest_stage, from_attributes=True
        )

    return response


@router.delete("/{contact_id}", status_code=204)
def delete_contact(contact_id: int, db: Session = Depends(get_db)):
    """Soft delete a contact."""
    contact = db.query(Contact).filter(Contact.id == contact_id).first()

    if not contact or contact.deleted_at:
        raise HTTPException(status_code=404, detail="Contact not found")

    from datetime import datetime

    contact.deleted_at = datetime.utcnow()
    db.add(contact)
    db.commit()


@router.put("/{contact_id}/consumer-profile", response_model=ConsumerProfileResponse)
def upsert_consumer_profile(
    contact_id: int,
    body: ConsumerProfileUpdate,
    db: Session = Depends(get_db),
):
    """Create or update a contact's consumer profile."""
    contact = db.query(Contact).filter(Contact.id == contact_id).first()

    if not contact or contact.deleted_at:
        raise HTTPException(status_code=404, detail="Contact not found")
    if contact.contact_type != "consumer":
        raise HTTPException(status_code=400, detail="Contact is not a consumer")

    data = body.model_dump(exclude_unset=True)
    if contact.consumer_profile is None:
        contact.consumer_profile = ConsumerProfile(**data)
    else:
        for field, value in data.items():
            setattr(contact.consumer_profile, field, value)

    db.commit()
    db.refresh(contact.consumer_profile)
    return ConsumerProfileResponse.model_validate(contact.consumer_profile, from_attributes=True)


@router.delete("/{contact_id}/consumer-profile", status_code=204)
def delete_consumer_profile(contact_id: int, db: Session = Depends(get_db)):
    """Remove a contact's consumer profile (the contact itself is kept)."""
    contact = db.query(Contact).filter(Contact.id == contact_id).first()

    if not contact or contact.deleted_at:
        raise HTTPException(status_code=404, detail="Contact not found")
    if contact.consumer_profile is None:
        raise HTTPException(status_code=404, detail="Consumer profile not found")

    db.delete(contact.consumer_profile)
    db.commit()


@router.get("/{contact_id}/stages", response_model=list[LeadStageResponse])
def get_contact_stages(
    contact_id: int,
    db: Session = Depends(get_db),
):
    """Get stage history for a contact (ordered by date)."""
    contact = db.query(Contact).filter(Contact.id == contact_id).first()

    if not contact or contact.deleted_at:
        raise HTTPException(status_code=404, detail="Contact not found")

    stages = (
        db.query(LeadStage)
        .filter(LeadStage.contact_id == contact_id)
        .order_by(LeadStage.changed_at.desc())
        .all()
    )

    return [LeadStageResponse.model_validate(s, from_attributes=True) for s in stages]


@router.post("/{contact_id}/stages", response_model=LeadStageResponse, status_code=201)
def add_contact_stage(
    contact_id: int,
    body: LeadStageCreate,
    db: Session = Depends(get_db),
):
    """Add a new stage to a contact's history."""
    contact = db.query(Contact).filter(Contact.id == contact_id).first()

    if not contact or contact.deleted_at:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Validate employee exists
    from models import Employee

    employee = db.query(Employee).filter(Employee.id == body.changed_by).first()
    if not employee:
        raise HTTPException(status_code=400, detail="Employee not found")

    # A lead only moves on from New Lead once its details are verified (Lost is always allowed)
    if body.stage not in (StageEnum.new_lead, StageEnum.lost):
        latest = (
            db.query(LeadStage)
            .filter(LeadStage.contact_id == contact_id)
            .order_by(LeadStage.id.desc())
            .first()
        )
        pending = missing_fields(contact)
        if (latest is None or latest.stage == "new_lead") and pending:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Complete the lead's details before moving it to the next stage",
                    "missing_fields": pending,
                },
            )

    # Create stage with contact_id from URL
    stage_data = body.model_dump()
    stage_data["contact_id"] = contact_id

    stage = LeadStage(**stage_data)
    db.add(stage)
    db.commit()
    db.refresh(stage)

    return LeadStageResponse.model_validate(stage, from_attributes=True)
