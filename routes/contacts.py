from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, aliased, joinedload
from auth import get_current_user
from database import get_db
from verification import missing_fields, verified_clause
from models import Contact, ConsumerProfile, Employee, LeadSource, LeadStage, PartnerProfile
from schemas import (
    ContactCreate,
    ContactListItem,
    ContactListResponse,
    ContactResponse,
    ContactType,
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


def _check_location(source: LeadSource | None, latitude, longitude) -> None:
    """GPS location is required for Offline (field visit) leads and not allowed otherwise."""
    if source is not None and source.slug == "offline":
        if latitude is None or longitude is None:
            raise HTTPException(
                status_code=400, detail="latitude and longitude are required for offline leads"
            )
    elif latitude is not None or longitude is not None:
        raise HTTPException(
            status_code=400, detail="latitude and longitude are only allowed for offline leads"
        )


@router.get("", response_model=ContactListResponse)
def list_contacts(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    contact_type: ContactType | None = None,
    profession_type: str | None = Query(None, description="Partner profession, e.g. Plumber"),
    district: str | None = None,
    search: str | None = Query(None, description="Matches name or phone"),
    stage: StageEnum | None = None,
    lead_source_id: int | None = None,
    assigned_to: int | None = None,
    created_by: int | None = None,
    verified: bool | None = Query(None, description="true = only verified, false = only unverified"),
    db: Session = Depends(get_db),
):
    """List contacts (any type) with current stage, lead source and owner."""
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

    def base(*entities):
        q = (
            db.query(*entities)
            .select_from(Contact)
            .outerjoin(latest, latest.c.contact_id == Contact.id)
            .outerjoin(current, current.id == latest.c.stage_id)
            .filter(Contact.deleted_at.is_(None))  # Exclude soft-deleted
        )
        if contact_type:
            q = q.filter(Contact.contact_type == contact_type.value)
        if profession_type:
            q = q.join(PartnerProfile, PartnerProfile.contact_id == Contact.id).filter(
                PartnerProfile.profession_type == profession_type
            )
        if district:
            q = q.filter(Contact.district.ilike(f"%{district}%"))
        if search:
            like = f"%{search.strip()}%"
            q = q.filter(or_(Contact.name.ilike(like), Contact.phone.ilike(like)))
        if stage:
            q = q.filter(current.stage == stage.value)
        if lead_source_id is not None:
            q = q.filter(Contact.lead_source_id == lead_source_id)
        if assigned_to is not None:
            q = q.filter(Contact.assigned_to == assigned_to)
        if created_by is not None:
            q = q.filter(Contact.created_by == created_by)
        if verified is True:
            q = q.filter(verified_clause())
        elif verified is False:
            q = q.filter(~verified_clause())
        return q

    total = base(func.count(Contact.id)).scalar() or 0
    rows = (
        base(Contact, current)
        .options(
            joinedload(Contact.lead_source),
            joinedload(Contact.assigned_employee),
            joinedload(Contact.creator),
            joinedload(Contact.partner_profile),
            joinedload(Contact.consumer_profile),
        )
        .order_by(Contact.created_at.desc(), Contact.id.desc())
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
                "profession_type": contact.partner_profile.profession_type if contact.partner_profile else None,
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
    _check_location(source, body.latitude, body.longitude)
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

    data = body.model_dump(exclude={"consumer_profile", "assigned_to"})
    contact = Contact(**data, assigned_to=assigned_to, created_by=current_user.id)
    if body.consumer_profile is not None:
        contact.consumer_profile = ConsumerProfile(**body.consumer_profile.model_dump())
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

    update_data = body.model_dump(exclude_unset=True, exclude={"consumer_profile"})
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

    # Create stage with contact_id from URL
    stage_data = body.model_dump()
    stage_data["contact_id"] = contact_id

    stage = LeadStage(**stage_data)
    db.add(stage)
    db.commit()
    db.refresh(stage)

    return LeadStageResponse.model_validate(stage, from_attributes=True)
