"""
Lead service — all business logic for the Leads module.

WHAT IS A SERVICE?
------------------
A service contains the "rules" of the application. Routes validate input and call
services; services do the actual work (creating leads, checking for duplicates,
assigning employees, etc.) and call the database.

Keeping business logic here (not in routes) means:
  - Routes stay thin and readable
  - Logic can be reused by multiple routes (e.g. the inbound route and the manual route
    both create leads — they both call create_lead() from here)
  - Logic is easier to test

FUNCTIONS IN THIS FILE:
    compute_score(lead)         — calculates the 0–100 quality score
    check_duplicate(db, ...)    — checks for existing lead or contact with same phone/email
    auto_assign(db)             — picks the least-loaded active employee for auto-assignment
    create_lead(db, ...)        — creates a new lead with score + dedup + auto-assign
    list_leads(db, ...)         — paginated, filtered list of leads
    get_lead(db, id)            — single lead by id
    update_lead(db, ...)        — partial update of a lead
    soft_delete_lead(db, id)    — sets deleted_at (never hard-deletes)
    convert_lead(db, ...)       — creates a Contact from the lead data
    get_lead_stats(db)          — counts by status and source
"""

import datetime
import logging
from datetime import timezone as _tz

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from models.contact import Contact
from models.employee import Employee
from models.lead import Lead
from phone_utils import phone_key as compute_phone_key

logger = logging.getLogger(__name__)


# ── Lead scoring ──────────────────────────────────────────────────────────────

def compute_score(lead: Lead) -> int:
    """
    Calculate a quality score (0–100) for a lead.

    WHY RULE-BASED?
        Simple rule-based scoring is transparent and predictable. Staff can understand
        why a lead has a certain score without needing data science expertise.
        Machine learning scoring can be added later once enough conversion data exists.

    HOW POINTS ARE EARNED:
        Source (quality of the channel):
          meta_ads → 30  (highest intent — they clicked a paid ad)
          website  → 25  (came to us proactively)
          whatsapp → 20  (reached out via messaging)
          manual   → 10  (staff-entered — quality unknown)

        Completeness (more data = easier to follow up):
          phone        → +10
          email        → +10
          last_name    → +5

        Engagement:
          whatsapp_sent  → +10 (we made contact)
          status = qualified → +5

        Max = 100 (capped even if rules total higher)
    """
    score = 0

    # Source points — keyed by lead_sources.id
    # 1=Walk-in, 2=Referral, 3=Meta Ads, 4=Website, 5=WhatsApp, 6=JustDial, 7=GMB, 8=Field Visit
    source_score = {1: 10, 2: 20, 3: 30, 4: 25, 5: 20, 6: 15, 7: 20, 8: 15}
    score += source_score.get(lead.source_id or 0, 10)

    # Completeness points
    if lead.phone:
        score += 10
    if lead.email:
        score += 10
    if lead.last_name:
        score += 5

    # Engagement points
    if lead.whatsapp_sent:
        score += 10
    if lead.status == "qualified":
        score += 5

    return min(score, 100)  # never exceed 100


# ── Duplicate detection ───────────────────────────────────────────────────────

def check_duplicate(
    db: Session,
    phone_key_val: str | None,
    email: str | None,
) -> tuple[Lead | None, Contact | None]:
    """
    Check whether a lead or contact with the same phone/email already exists.

    Returns (lead, contact) — at most one of these will be non-None.
    If both are None, there is no duplicate.

    HOW phone_key WORKS:
        Phone numbers can be entered in many formats:
            9000000003
            +91 9000000003
            091-90000-00003
        We normalise to the last 10 digits ("phone_key").
        '9000000003' and '+91 9000000003' both become '9000000003',
        so we detect duplicates regardless of how the number was formatted.
    """
    if not phone_key_val and not email:
        return None, None

    # Check leads table first
    lead_q = select(Lead).where(Lead.deleted_at.is_(None))
    if phone_key_val and email:
        lead_q = lead_q.where(
            or_(Lead.phone_key == phone_key_val, Lead.email == email)
        )
    elif phone_key_val:
        lead_q = lead_q.where(Lead.phone_key == phone_key_val)
    else:
        lead_q = lead_q.where(Lead.email == email)

    existing_lead = db.scalar(lead_q.limit(1))
    if existing_lead:
        return existing_lead, None

    # Contact dedup skipped — contacts table not migrated yet.
    # Restore this check when the contacts module is built.
    return None, None


# ── Auto-assignment ───────────────────────────────────────────────────────────

def auto_assign(db: Session) -> int | None:
    """
    Pick the active employee with the fewest open leads (round-robin by workload).

    WHY LEAST-LOADED?
        Pure round-robin (1→2→3→1→...) ignores differences in lead complexity.
        Least-loaded ensures no one is overwhelmed while others are idle.

    Returns:
        employee.id of the chosen employee, or None if no eligible employee found.
    """
    # Count open (non-converted, non-lost) leads per active employee.
    # LEFT JOIN so employees with zero leads are included.
    subq = (
        select(
            Lead.assigned_to.label("emp_id"),
            func.count(Lead.id).label("open_leads"),
        )
        .where(Lead.deleted_at.is_(None))
        .where(Lead.status.notin_(["converted", "lost"]))
        .group_by(Lead.assigned_to)
        .subquery()
    )

    result = db.execute(
        select(
            Employee.id,
            func.coalesce(subq.c.open_leads, 0).label("cnt"),
        )
        .outerjoin(subq, Employee.id == subq.c.emp_id)
        .where(Employee.is_active == 1)
        .order_by(func.coalesce(subq.c.open_leads, 0).asc(), Employee.id.asc())
        .limit(1)
    ).first()

    return result.id if result else None


# ── Create ────────────────────────────────────────────────────────────────────

def create_lead(
    db: Session,
    schema: str,
    first_name: str,
    source_id: int | None = None,
    last_name: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    notes: str | None = None,
    address: str | None = None,
    tags: list[str] | None = None,
    assigned_to: int | None = None,
    raw_payload: dict | None = None,
    allow_duplicate: bool = False,
    background_tasks: BackgroundTasks | None = None,
) -> Lead:
    """
    Create a new lead.

    Steps:
      1. Compute phone_key from phone
      2. Dedup check (unless allow_duplicate=True)
      3. Auto-assign if assigned_to not provided
      4. Compute initial score
      5. INSERT lead row
      6. Queue WhatsApp welcome message (background task, best-effort)

    Raises:
      HTTPException(409) — duplicate lead or contact found (unless allow_duplicate=True)
    """
    pk = compute_phone_key(phone) if phone else None

    # Duplicate check
    if not allow_duplicate:
        dup_lead, dup_contact = check_duplicate(db, pk, email)
        if dup_lead:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "duplicate_lead",
                    "message": "A lead with this phone or email already exists.",
                    "existing_id": dup_lead.id,
                    "existing_type": "lead",
                    "existing_name": dup_lead.first_name,
                },
            )
        if dup_contact:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "duplicate_contact",
                    "message": "A contact with this phone already exists.",
                    "existing_id": dup_contact.id,
                    "existing_type": "contact",
                    "existing_name": dup_contact.name,
                },
            )

    # Auto-assign if not specified
    if assigned_to is None:
        assigned_to = auto_assign(db)

    # Build the lead object (score computed after creation when we have all fields)
    lead = Lead(
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        phone_key=pk,
        email=email,
        source_id=source_id,
        notes=notes,
        address=address,
        tags=tags,
        raw_payload=raw_payload,
        assigned_to=assigned_to,
        score=0,  # placeholder; re-computed below
    )
    lead.score = compute_score(lead)
    db.add(lead)
    db.commit()
    db.refresh(lead)

    # Queue WhatsApp welcome (non-blocking background task)
    if phone and background_tasks is not None:
        # Import here to avoid circular imports
        from services.whatsapp_service import send_lead_welcome
        background_tasks.add_task(send_lead_welcome, lead.id, schema)

    return lead


# ── Read ──────────────────────────────────────────────────────────────────────

def list_leads(
    db: Session,
    status: str | None = None,
    source_id: int | None = None,
    assigned_to: int | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> dict:
    """
    Return a paginated, filtered list of leads.

    HOW PAGINATION WORKS:
        skip=0, limit=20 → returns leads 1–20
        skip=20, limit=20 → returns leads 21–40
        The frontend uses `total` to calculate the number of pages.
    """
    q = (
        select(Lead)
        .where(Lead.deleted_at.is_(None))
    )

    if status:
        q = q.where(Lead.status == status)
    if source_id:
        q = q.where(Lead.source_id == source_id)
    if assigned_to:
        q = q.where(Lead.assigned_to == assigned_to)
    if date_from:
        q = q.where(Lead.created_at >= datetime.datetime.combine(date_from, datetime.time.min))
    if date_to:
        q = q.where(Lead.created_at <= datetime.datetime.combine(date_to, datetime.time.max))
    if search:
        term = f"%{search}%"
        q = q.where(
            or_(
                Lead.first_name.ilike(term),
                Lead.last_name.ilike(term),
                Lead.phone.ilike(term),
                Lead.email.ilike(term),
            )
        )

    total = db.scalar(select(func.count()).select_from(q.subquery()))
    leads = db.scalars(q.order_by(Lead.created_at.desc()).offset(skip).limit(limit)).all()

    # Denormalise assigned_name (employees table is small — no JOIN needed)
    emp_ids = {l.assigned_to for l in leads if l.assigned_to}
    emp_map: dict[int, str] = {}
    if emp_ids:
        rows = db.execute(
            select(Employee.id, Employee.name).where(Employee.id.in_(emp_ids))
        ).all()
        emp_map = {r.id: r.name for r in rows}

    # Denormalise source_name (lead_sources table is tiny)
    from models.lead_source import LeadSource as LeadSourceModel
    src_ids = {l.source_id for l in leads if l.source_id}
    src_map: dict[int, str] = {}
    if src_ids:
        src_rows = db.execute(
            select(LeadSourceModel.id, LeadSourceModel.name).where(LeadSourceModel.id.in_(src_ids))
        ).all()
        src_map = {r.id: r.name for r in src_rows}

    items = []
    for lead in leads:
        item = {k: v for k, v in lead.__dict__.items() if not k.startswith("_")}
        item["assigned_name"] = emp_map.get(lead.assigned_to) if lead.assigned_to else None
        item["source_name"] = src_map.get(lead.source_id) if lead.source_id else None
        items.append(item)

    return {"items": items, "total": total or 0, "skip": skip, "limit": limit}


def get_lead(db: Session, lead_id: int) -> Lead:
    """Fetch one lead by id. Raises 404 if not found or soft-deleted."""
    lead = db.scalar(
        select(Lead)
        .where(Lead.id == lead_id)
        .where(Lead.deleted_at.is_(None))
    )
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


def get_lead_detail(db: Session, lead_id: int) -> dict:
    """Fetch a lead and enrich it with source_name for the detail response."""
    lead = get_lead(db, lead_id)
    result = {k: v for k, v in lead.__dict__.items() if not k.startswith("_")}
    if lead.source_id:
        from models.lead_source import LeadSource as LeadSourceModel
        src = db.scalar(select(LeadSourceModel).where(LeadSourceModel.id == lead.source_id))
        result["source_name"] = src.name if src else None
    else:
        result["source_name"] = None
    return result


# ── Update ────────────────────────────────────────────────────────────────────

def update_lead(db: Session, lead_id: int, **fields) -> Lead:
    """
    Apply a partial update to a lead.

    Only the fields in `fields` are changed — fields not passed are left as-is.
    Re-computes the score after any change.
    """
    lead = get_lead(db, lead_id)

    for key, value in fields.items():
        if value is not None:
            setattr(lead, key, value)

    # Recompute phone_key if phone changed
    if "phone" in fields and fields["phone"]:
        lead.phone_key = compute_phone_key(fields["phone"])

    lead.score = compute_score(lead)
    db.commit()
    db.refresh(lead)
    return lead


def soft_delete_lead(db: Session, lead_id: int) -> None:
    """Mark a lead as deleted without removing the row."""
    lead = get_lead(db, lead_id)
    lead.deleted_at = datetime.datetime.now(_tz.utc)
    db.commit()


# ── Convert to Contact ────────────────────────────────────────────────────────

def convert_lead(db: Session, lead_id: int, current_user_id: int) -> dict:
    """
    Convert a lead into a Contact.

    HOW IT WORKS:
      1. Check the lead exists and hasn't already been converted.
      2. If a contact with the same phone already exists, link to it without creating a duplicate.
      3. Otherwise create a new Contact row from the lead's data.
      4. Update the lead: status="converted", contact_id, converted_at.

    WHAT IS CREATED:
      A new Contact row with:
        - name = "{first_name} {last_name}"
        - phone, phone_key (from lead)
        - notes (from lead)
        - assigned_to (from lead)
        - created_by = current_user_id

    Returns:
      {"lead_id": ..., "contact_id": ..., "created": True/False}
      `created=False` means we linked to an existing contact (not a new one).
    """
    lead = get_lead(db, lead_id)

    if lead.status == "converted":
        raise HTTPException(status_code=409, detail="Lead has already been converted")

    # Contacts table not yet built — mark lead as converted without creating a contact.
    # Restore the full contact-creation logic once the contacts module is migrated.
    lead.status = "converted"
    lead.converted_at = datetime.datetime.now(_tz.utc)
    db.commit()
    return {"lead_id": lead.id, "contact_id": None, "created": False}


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_lead_stats(db: Session) -> dict:
    """
    Count leads by status and by source for the dashboard widget.

    Returns:
        {
          "by_status": {"new": 12, "contacted": 5, ...},
          "by_source": {"website": 7, "whatsapp": 10, ...},
          "total": 17
        }
    """
    status_rows = db.execute(
        select(Lead.status, func.count(Lead.id).label("cnt"))
        .where(Lead.deleted_at.is_(None))
        .group_by(Lead.status)
    ).all()

    source_rows = db.execute(
        select(Lead.source_id, func.count(Lead.id).label("cnt"))
        .where(Lead.deleted_at.is_(None))
        .group_by(Lead.source_id)
    ).all()

    # Map source_id → name from lead_sources
    from models.lead_source import LeadSource as LeadSourceModel
    all_src_ids = {r.source_id for r in source_rows if r.source_id}
    src_name_map: dict[int, str] = {}
    if all_src_ids:
        name_rows = db.execute(
            select(LeadSourceModel.id, LeadSourceModel.name).where(LeadSourceModel.id.in_(all_src_ids))
        ).all()
        src_name_map = {r.id: r.name for r in name_rows}

    by_status = {r.status: r.cnt for r in status_rows}
    by_source = {src_name_map.get(r.source_id, "Unknown"): r.cnt for r in source_rows}
    total = sum(by_status.values())

    return {"by_status": by_status, "by_source": by_source, "total": total}
