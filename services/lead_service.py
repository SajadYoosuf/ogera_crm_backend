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
    list_lead_statuses(db)      — active rows of the lead_statuses table
    ensure_valid_status(db, s)  — 400s unless s is an active status slug or "converted"
"""

import datetime
import logging
from datetime import timezone as _tz

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from models.contact import Contact
from models.employee import Employee
from models.lead import Lead
from business_time import ist_day_start_utc
from models.lead_status import LeadStatus
from phone_utils import phone_key as compute_phone_key

logger = logging.getLogger(__name__)


def _preload_display_relations(lead: Lead) -> None:
    """
    Touch lead.creator and lead.branch once while the session is still open.

    WHY THIS IS NEEDED:
        LeadResponse includes created_by_name/branch_name, which read these two
        relationships lazily (see the @property on the Lead model). create_lead
        and update_lead return the raw Lead object from inside a
        `with get_session_for_schema(...)` block; by the time FastAPI serializes
        the response, that block has already exited and closed the session, so
        any NOT-yet-loaded relationship raises DetachedInstanceError. Accessing
        them here, before the session closes, caches the values on the object
        so they're safe to read after detachment.
    """
    _ = lead.creator
    _ = lead.branch


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

    WHY ADMIN AND MANAGER ARE EXCLUDED?
        Admins and managers oversee the pipeline and should not be handed leads
        automatically. Auto-assigned leads go to other active employees only. A
        head-created lead with no explicit assignee therefore lands on a staff
        member; an admin or manager can still assign a lead to themselves by hand.

    Returns:
        employee.id of the chosen employee, or None if no eligible employee found.
    """
    from models.role import Role

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
        .join(Role, Role.id == Employee.role_id)
        .outerjoin(subq, Employee.id == subq.c.emp_id)
        .where(Employee.is_active == 1)
        .where(Role.slug.notin_(("admin", "manager")))
        .order_by(func.coalesce(subq.c.open_leads, 0).asc(), Employee.id.asc())
        .limit(1)
    ).first()

    return result.id if result else None


# ── Notifications ─────────────────────────────────────────────────────────────

def notify_lead_assigned(db: Session, lead: Lead, actor_id: int | None) -> None:
    """Tell the assignee a lead was assigned to them (skipped when they assigned it to themselves)."""
    if not lead.assigned_to or lead.assigned_to == actor_id:
        return
    from services.notification_service import create_notification
    name = " ".join(p for p in (lead.first_name, lead.last_name) if p) or "a lead"
    create_notification(db, lead.assigned_to, "lead_assigned", "Lead assigned to you",
                        f"{name} was assigned to you.", "lead", lead.id)


def _notify_lead_created(db: Session, lead: Lead, created_by: int | None) -> None:
    """Notify the assignee, and tell heads about every new lead (manual or auto-captured)."""
    from services.notification_service import create_notification
    from models.role import Role
    notify_lead_assigned(db, lead, created_by)
    name = " ".join(p for p in (lead.first_name, lead.last_name) if p) or "A lead"
    kind = "Lead added manually" if created_by else "New lead received"
    # Name who added it, so heads can tell at a glance who entered the lead.
    creator = db.get(Employee, created_by) if created_by else None
    by = f" by {creator.name}" if creator else ""
    head_ids = db.scalars(
        select(Employee.id)
        .join(Role, Role.id == Employee.role_id)
        .where(Role.slug.in_(("admin", "manager")))
        .where(Employee.is_active == 1)
        .where(Employee.deleted_at.is_(None))
    ).all()
    for hid in head_ids:
        if hid in (created_by, lead.assigned_to):
            continue
        create_notification(db, hid, "lead_created", kind, f"{name} was added{by}.", "lead", lead.id)


def _notify_lead_created_safely(db: Session, lead: Lead, created_by: int | None) -> None:
    """
    Run the notification step inside a savepoint. A failure here (e.g. a schema
    mismatch on the notification queries) only drops the notifications; the lead
    itself is still saved. The error is logged so it can be fixed.
    """
    try:
        with db.begin_nested():
            _notify_lead_created(db, lead, created_by)
    except SQLAlchemyError as exc:
        logger.error("lead %s saved but notifications were skipped — %s", lead.id, exc)


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
    created_at: datetime.datetime | None = None,
    created_by: int | None = None,
    branch_id: int | None = None,
) -> Lead:
    """
    Create a new lead.

    Steps:
      1. Compute phone_key from phone
      2. Dedup check (unless allow_duplicate=True)
      3. Auto-assign if assigned_to not provided
      4. Default branch_id to the creating employee's own branch, if any
      5. Compute initial score
      6. INSERT lead row (created_by = the employee who entered it, or None)
      7. Queue WhatsApp welcome message (background task, best-effort)

    created_by: employee.id of the staff member creating the lead (manual entry
    or an employee-run import). Pass None for the webhook/inbound channels
    (website form, Meta Ads sheet, WhatsApp) — they have no logged-in employee.

    branch_id: pass an explicit id to pick a branch, or omit it to default to
    the creating employee's own branch. Only looked up when created_by is set —
    webhook/inbound leads have no employee, so they stay branch-less.

    Raises:
      HTTPException(409) — duplicate lead or contact found (unless allow_duplicate=True)
    """
    pk = compute_phone_key(phone) if phone else None

    if branch_id is None and created_by is not None:
        creator = db.scalar(select(Employee).where(Employee.id == created_by))
        if creator is not None:
            branch_id = creator.branch_id

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
        created_by=created_by,
        branch_id=branch_id,
        score=0,  # placeholder; re-computed below
        # created_at override: used when importing historical leads so the
        # original lead date is preserved rather than defaulting to now()
        **({"created_at": created_at} if created_at is not None else {}),
    )
    lead.score = compute_score(lead)
    db.add(lead)
    try:
        db.flush()
        from services.record_log_service import add_log
        add_log(db, "lead", lead.id, "created", created_by)
        _notify_lead_created_safely(db, lead, created_by)
        db.commit()
        db.refresh(lead)
        _preload_display_relations(lead)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("create_lead — commit failed: %s", exc)
        raise HTTPException(503, detail="Could not save. Please try again.")

    # Queue WhatsApp welcome (non-blocking background task)
    if phone and background_tasks is not None:
        # Import here to avoid circular imports
        from services.whatsapp_service import send_lead_welcome
        background_tasks.add_task(send_lead_welcome, lead.id, schema)

    return lead


# ── Read ──────────────────────────────────────────────────────────────────────

def list_leads(
    db: Session,
    current_employee: "Employee | None" = None,
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

    ROLE SCOPE:
        staff (non-head roles) only see leads where assigned_to == their id.
        heads (admin, manager) see all leads.

    HOW PAGINATION WORKS:
        skip=0, limit=20 → returns leads 1–20
        skip=20, limit=20 → returns leads 21–40
        The frontend uses `total` to calculate the number of pages.
    """
    from services.lead_access import is_head

    q = (
        select(Lead)
        .where(Lead.deleted_at.is_(None))
    )

    # Role scope: staff only see their own leads
    if current_employee is not None and not is_head(current_employee):
        q = q.where(Lead.assigned_to == current_employee.id)

    if status:
        q = q.where(Lead.status == status)
    if source_id:
        q = q.where(Lead.source_id == source_id)
    if assigned_to:
        q = q.where(Lead.assigned_to == assigned_to)
    if date_from:
        q = q.where(Lead.created_at >= ist_day_start_utc(date_from))
    if date_to:
        q = q.where(Lead.created_at < ist_day_start_utc(date_to + datetime.timedelta(days=1)))
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

    # Denormalise assigned_name and created_by_name — both are employees.id,
    # and the employees table is small enough that one combined lookup beats a JOIN.
    emp_ids = {l.assigned_to for l in leads if l.assigned_to} | {l.created_by for l in leads if l.created_by}
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

    # Denormalise branch_name
    from models.branch import Branch
    branch_ids = {l.branch_id for l in leads if l.branch_id}
    branch_map: dict[int, str] = {}
    if branch_ids:
        branch_rows = db.execute(
            select(Branch.id, Branch.name).where(Branch.id.in_(branch_ids))
        ).all()
        branch_map = {r.id: r.name for r in branch_rows}

    items = []
    for lead in leads:
        item = {k: v for k, v in lead.__dict__.items() if not k.startswith("_")}
        item["assigned_name"] = emp_map.get(lead.assigned_to) if lead.assigned_to else None
        item["created_by_name"] = emp_map.get(lead.created_by) if lead.created_by else None
        item["source_name"] = src_map.get(lead.source_id) if lead.source_id else None
        item["branch_name"] = branch_map.get(lead.branch_id) if lead.branch_id else None
        items.append(item)

    return {"items": items, "total": total or 0, "skip": skip, "limit": limit}


def get_lead(db: Session, lead_id: int) -> Lead:
    """Fetch one lead by id. Raises 404 if not found or soft-deleted."""
    try:
        lead = db.scalar(
            select(Lead)
            .where(Lead.id == lead_id)
            .where(Lead.deleted_at.is_(None))
        )
    except SQLAlchemyError as exc:
        logger.error("get_lead — query failed: %s", exc)
        raise HTTPException(status_code=503, detail="Server unavailable, please retry in a moment.")
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


def get_lead_detail(db: Session, lead_id: int) -> dict:
    """Fetch a lead and enrich it with source_name, created_by_name and branch_name."""
    lead = get_lead(db, lead_id)
    result = {k: v for k, v in lead.__dict__.items() if not k.startswith("_")}
    try:
        if lead.source_id:
            from models.lead_source import LeadSource as LeadSourceModel
            src = db.scalar(select(LeadSourceModel).where(LeadSourceModel.id == lead.source_id))
            result["source_name"] = src.name if src else None
        else:
            result["source_name"] = None

        if lead.created_by:
            creator = db.scalar(select(Employee).where(Employee.id == lead.created_by))
            result["created_by_name"] = creator.name if creator else None
        else:
            result["created_by_name"] = None

        if lead.assigned_to:
            assignee = db.scalar(select(Employee).where(Employee.id == lead.assigned_to))
            result["assigned_name"] = assignee.name if assignee else None
        else:
            result["assigned_name"] = None

        if lead.branch_id:
            from models.branch import Branch
            branch = db.scalar(select(Branch).where(Branch.id == lead.branch_id))
            result["branch_name"] = branch.name if branch else None
        else:
            result["branch_name"] = None
    except SQLAlchemyError as exc:
        logger.error("get_lead_detail — enrichment query failed: %s", exc)
        raise HTTPException(status_code=503, detail="Server unavailable, please retry in a moment.")
    return result


# ── Update ────────────────────────────────────────────────────────────────────

def update_lead(db: Session, lead_id: int, *, actor_id: int | None = None, **fields) -> Lead:
    """
    Apply a partial update to a lead.

    Only the fields in `fields` are changed — fields not passed are left as-is.
    Re-computes the score after any change. Status and assignee changes are
    written to record_logs (the lead's timeline); other edits as one "edited" row.
    """
    from services.record_log_service import add_log

    lead = get_lead(db, lead_id)
    changed = {k for k, v in fields.items() if v is not None and getattr(lead, k, None) != v}
    if "status" in changed:
        add_log(db, "lead", lead_id, "status_changed", actor_id, lead.status, fields["status"])
    if "assigned_to" in changed:
        add_log(db, "lead", lead_id, "assigned", actor_id, lead.assigned_to, fields["assigned_to"])
    if changed - {"status", "assigned_to"}:
        add_log(db, "lead", lead_id, "edited", actor_id, None, ", ".join(sorted(changed - {"status", "assigned_to"})))

    for key, value in fields.items():
        if value is not None:
            setattr(lead, key, value)

    # Recompute phone_key if phone changed
    if "phone" in fields and fields["phone"]:
        lead.phone_key = compute_phone_key(fields["phone"])

    lead.score = compute_score(lead)
    try:
        db.commit()
        db.refresh(lead)
        _preload_display_relations(lead)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("update_lead — commit failed: %s", exc)
        raise HTTPException(503, detail="Could not save. Please try again.")
    return lead


def soft_delete_lead(db: Session, lead_id: int) -> None:
    """Mark a lead as deleted without removing the row."""
    lead = get_lead(db, lead_id)
    lead.deleted_at = datetime.datetime.now(_tz.utc)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("soft_delete_lead — commit failed: %s", exc)
        raise HTTPException(503, detail="Could not save. Please try again.")


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

    # Check if a live contact with the same phone already exists.
    # This avoids creating a duplicate when the same person was both a lead and an
    # existing contact (e.g. they were manually added before the lead came in).
    existing: Contact | None = None
    if lead.phone_key:
        existing = (
            db.query(Contact)
            .filter(
                Contact.phone_key == lead.phone_key,
                Contact.deleted_at.is_(None),
            )
            .first()
        )

    if existing:
        # Link lead to the already-existing contact (no new row created).
        contact_id = existing.id
        created = False
    else:
        # Build the full name from first_name + last_name fields on the lead.
        name_parts = [p for p in [getattr(lead, "first_name", None), getattr(lead, "last_name", None)] if p]
        full_name = " ".join(name_parts).strip() or None

        new_contact = Contact(
            name=full_name,
            phone=lead.phone or "",
            phone_key=lead.phone_key or "",
            contact_type="consumer",
            notes=lead.notes,
            address=getattr(lead, "address", None),
            assigned_to=lead.assigned_to,
            created_by=current_user_id,
            lead_source_id=getattr(lead, "source_id", None),
            lead_id=lead.id,
        )
        db.add(new_contact)
        try:
            db.flush()   # get new_contact.id before the commit
        except SQLAlchemyError as exc:
            db.rollback()
            logger.error("convert_lead — flush failed: %s", exc)
            raise HTTPException(503, detail="Could not save. Please try again.")
        contact_id = new_contact.id
        created = True

    # Mark the lead as converted and store the contact link.
    old_status = lead.status
    lead.status = "converted"
    lead.converted_at = datetime.datetime.now(_tz.utc)
    if hasattr(lead, "contact_id"):
        lead.contact_id = contact_id
    from services.record_log_service import add_log
    add_log(db, "lead", lead.id, "converted", current_user_id, old_status, contact_id)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("convert_lead — commit failed: %s", exc)
        raise HTTPException(503, detail="Could not save. Please try again.")
    return {"lead_id": lead.id, "contact_id": contact_id, "created": created}


# ── Statuses ──────────────────────────────────────────────────────────────────

def list_lead_statuses(db: Session) -> list[LeadStatus]:
    """Active rows of the lead_statuses table, in dropdown order."""
    return db.scalars(
        select(LeadStatus)
        .where(LeadStatus.is_active.is_(True))
        .order_by(LeadStatus.sort_order, LeadStatus.id)
    ).all()


def ensure_valid_status(db: Session, status: str) -> None:
    """
    Raise 400 unless `status` is an active lead_statuses slug or "converted".

    "converted" is accepted even though it is not a row in lead_statuses — it is
    the terminal value POST /leads/{id}/convert sets, and this lets that be
    undone by hand (PUT status back to converted) if it was set in error.
    """
    if status == "converted":
        return
    exists = db.scalar(
        select(LeadStatus.id).where(LeadStatus.slug == status, LeadStatus.is_active.is_(True))
    )
    if not exists:
        raise HTTPException(status_code=400, detail=f"'{status}' is not a valid lead status")


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

    # Unassigned breakdown by source — used by the Bulk Assign modal.
    unassigned_rows = db.execute(
        select(Lead.source_id, func.count(Lead.id).label("cnt"))
        .where(Lead.deleted_at.is_(None), Lead.assigned_to.is_(None))
        .group_by(Lead.source_id)
    ).all()
    unassigned_by_source = {
        src_name_map.get(r.source_id, "Unknown"): r.cnt for r in unassigned_rows
    }

    return {
        "by_status": by_status,
        "by_source": by_source,
        "total": total,
        "unassigned_by_source": unassigned_by_source,
    }


# ── Bulk assign ───────────────────────────────────────────────────────────────

def bulk_assign_leads(db: Session, assignments: list[dict]) -> int:
    """
    Assign batches of unassigned leads to employees by source.

    HOW IT WORKS:
      For each rule {source_id, assigned_to, count}:
        1. Find the N oldest unassigned leads from that source (or any source if
           source_id is None), ordered by created_at ASC so the earliest enquiries
           get picked up first.
        2. UPDATE their assigned_to to the given employee id.

    Returns total number of rows updated across all rules.

    WHY ORDER BY created_at ASC?
        Fairness — leads that have been waiting longest get assigned first.
        This is consistent with how most CRMs prioritise assignment queues.
    """
    total_assigned = 0

    for rule in assignments:
        source_id = rule.get("source_id")
        assigned_to = rule["assigned_to"]
        count = rule["count"]

        query = (
            select(Lead.id)
            .where(Lead.deleted_at.is_(None), Lead.assigned_to.is_(None))
            .order_by(Lead.created_at.asc())
            .limit(count)
        )
        if source_id is not None:
            query = query.where(Lead.source_id == source_id)

        lead_ids = [row[0] for row in db.execute(query).all()]
        if not lead_ids:
            continue

        db.execute(
            Lead.__table__.update()
            .where(Lead.id.in_(lead_ids))
            .values(
                assigned_to=assigned_to,
                updated_at=datetime.datetime.now(_tz.utc),
            )
        )
        total_assigned += len(lead_ids)

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("bulk_assign_leads — commit failed: %s", exc)
        raise HTTPException(503, detail="Could not save. Please try again.")
    return total_assigned
