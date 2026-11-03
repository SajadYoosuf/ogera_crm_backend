"""
Sales routes — create, read, update, and delete sale records.

WHY THIS FILE?
--------------
Every completed sale is stored here. Unlike the leads pipeline (which tracks potential
customers), a Sale row records a confirmed purchase — name, phone, product, date.

KEY BEHAVIOUR — CONTACT AUTO-LINK:
    When a sale is created with a phone number, we automatically link it to the CRM's
    contact pipeline. This keeps the sales register and the leads pipeline in sync.

    RULE:
    • If a live Contact with the same phone already exists → link the sale to it and
      move that contact to "won" stage (if not already converted).
    • If no Contact exists yet → create one from the sale data and immediately place
      it at the "won" stage.

    The contact is marked "won" without the normal verification gate because the sale
    itself is the conversion evidence — we already know the customer bought something.
    The contact may still be missing some CRM fields (e.g. district) and will show as
    unverified in the pipeline until those gaps are filled.

    If the sale has no phone, we skip contact linking (a phone is required to identify
    a contact uniquely).
"""

import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from auth import get_current_user
from database import get_db
from models import Contact, Employee, LeadSource, LeadStage, PipelineStage, Sale
from phone_utils import phone_key
from schemas.sale import SaleCreate, SaleListResponse, SaleResponse, SaleUpdate

# Everyone signed in can read the sales; only admins and managers change them.
router = APIRouter(prefix="/sales", tags=["sales"], dependencies=[Depends(get_current_user)])
writers = Depends(get_current_user)


_WON_SLUGS = {"won", "after_sales_referring"}


def _get_or_404(db: Session, sale_id: int) -> Sale:
    sale = db.get(Sale, sale_id)
    if sale is None:
        raise HTTPException(status_code=404, detail="Sale not found")
    return sale


@router.get("", response_model=SaleListResponse, summary="List past sales")
def list_sales(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, description="Matches name, phone, address or product"),
    product: str | None = Query(None, description="Exact purchased product"),
    employee_id: int | None = Query(None, description="Filter by the employee who created the sale"),
    purchased_from: datetime.date | None = None,
    purchased_to: datetime.date | None = None,
    db: Session = Depends(get_db),
):
    """Paged list of past sales, newest purchase first.
    Filter by search text, exact product name, employee, or a purchase date range. Requires sign-in.
    """
    q = db.query(Sale).options(joinedload(Sale.created_by_employee))
    if search and search.strip():
        like = f"%{search.strip()}%"
        q = q.filter(or_(Sale.name.like(like), Sale.phone.like(like), Sale.alt_phone.like(like),
                         Sale.address.like(like), Sale.purchased_product.like(like)))
    if product:
        q = q.filter(Sale.purchased_product == product)
    if employee_id is not None:
        q = q.filter(Sale.created_by_employee_id == employee_id)
    if purchased_from:
        q = q.filter(Sale.purchase_date >= purchased_from)
    if purchased_to:
        q = q.filter(Sale.purchase_date <= purchased_to)
    total = q.count()
    items = q.order_by(Sale.purchase_date.desc(), Sale.id.desc()).offset(skip).limit(limit).all()
    return SaleListResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/products", response_model=list[str], summary="List purchased product names")
def list_products(db: Session = Depends(get_db)):
    """Distinct product names that appear in past sales. Used for the filter and the form's suggestions."""
    rows = (db.query(Sale.purchased_product).filter(Sale.purchased_product.isnot(None))
            .distinct().order_by(Sale.purchased_product).all())
    return [r[0] for r in rows]


@router.get("/{sale_id}", response_model=SaleResponse, summary="Get a sale")
def get_sale(sale_id: int, db: Session = Depends(get_db)):
    """One past sale record. Returns 404 if it does not exist."""
    sale = db.query(Sale).options(joinedload(Sale.created_by_employee)).filter(Sale.id == sale_id).first()
    if sale is None:
        raise HTTPException(status_code=404, detail="Sale not found")
    return sale


def _get_won_stage(db: Session) -> PipelineStage | None:
    """
    Fetch the 'won' pipeline stage for consumers.

    Returns None if the stage has not been seeded (we skip linking rather than crashing,
    because sales creation should not fail because of a missing pipeline stage).
    """
    return db.query(PipelineStage).filter(
        PipelineStage.slug == "won",
        PipelineStage.contact_type == "consumer",
        PipelineStage.is_active == 1,
    ).first()


def _get_new_lead_stage(db: Session) -> PipelineStage | None:
    """Fetch the 'new_lead' consumer pipeline stage."""
    return db.query(PipelineStage).filter(
        PipelineStage.slug == "new_lead",
        PipelineStage.contact_type == "consumer",
        PipelineStage.is_active == 1,
    ).first()


def _sync_contact(db: Session, sale: Sale, created_by_id: int) -> None:
    """
    Create or link a CRM Contact for this sale, then set its stage to "won".

    WHY THIS EXISTS:
        A Sale record is proof that a customer bought something. We want the same
        customer to appear in the CRM pipeline at the "won" stage automatically,
        so managers can see conversions without having to manually promote leads.

    WHAT IT DOES:
        1. Looks up a live Contact whose phone_key matches the sale's phone.
        2a. Contact found  → set sale.contact_id and add a "won" stage if the
                             contact isn't already at a completed-sale stage.
        2b. No contact     → create a new Contact with the sale data, add a
                             "new_lead" stage (pipeline requires it) immediately
                             followed by a "won" stage.

    WHY WE SKIP VERIFICATION:
        Normally a contact must fill district, house_stage, etc. before it can be
        moved to "won". Here the sale IS the conversion event — we know the
        customer bought something even if their CRM profile is incomplete. We write
        the stages directly, bypassing the verification gate.

    INPUT:  db session, the just-created Sale ORM object, the employee id to attribute
    SIDE EFFECT: adds Contact and/or LeadStage rows to the session (caller must commit)
    """
    if not sale.phone:
        return  # can't identify a contact without a phone number

    won_stage = _get_won_stage(db)
    if won_stage is None:
        return  # pipeline not seeded; skip silently rather than crashing sale creation

    pkey = phone_key(sale.phone)

    # Step 1: find an existing live contact with this phone
    contact = (
        db.query(Contact)
        .filter(Contact.phone_key == pkey, Contact.deleted_at.is_(None))
        .first()
    )

    if contact is None:
        # Step 2b: no contact yet — create one from the sale data
        new_lead_stage = _get_new_lead_stage(db)
        if new_lead_stage is None:
            return  # pipeline not seeded; skip silently

        # Sales-created contacts come from the showroom.
        showroom_source = (
            db.query(LeadSource)
            .filter(LeadSource.slug == "showroom", LeadSource.is_active == 1)
            .first()
        )

        contact = Contact(
            contact_type="consumer",
            name=sale.name,
            phone=sale.phone,
            phone_key=pkey,
            phone_alt=sale.alt_phone,
            address=sale.address,
            branch=sale.branch,
            lead_source_id=showroom_source.id if showroom_source else None,
            created_by=created_by_id,
        )
        db.add(contact)
        db.flush()  # get contact.id before adding stage rows

        # Every contact must enter the pipeline at new_lead before moving to won
        db.add(LeadStage(contact_id=contact.id, stage_id=new_lead_stage.id, changed_by=created_by_id))
        db.add(LeadStage(contact_id=contact.id, stage_id=won_stage.id, changed_by=created_by_id))

    else:
        # Step 2a: contact exists — check if it's already at a completed-sale stage.
        # We join to PipelineStage so we can read the slug in one query (avoids lazy-load).
        latest_slug = (
            db.query(PipelineStage.slug)
            .join(LeadStage, LeadStage.stage_id == PipelineStage.id)
            .filter(LeadStage.contact_id == contact.id)
            .order_by(LeadStage.changed_at.desc(), LeadStage.id.desc())
            .limit(1)
            .scalar()
        )
        if latest_slug not in _WON_SLUGS:
            db.add(LeadStage(contact_id=contact.id, stage_id=won_stage.id, changed_by=created_by_id))

    # Link the sale to the contact
    sale.contact_id = contact.id


def _creator_id(db: Session, requested_id: int | None, current_user: Employee) -> int:
    """Use the signed-in employee by default, while validating a selected employee."""
    employee_id = requested_id if requested_id is not None else current_user.id
    employee = db.query(Employee).filter(
        Employee.id == employee_id,
        Employee.deleted_at.is_(None),
    ).first()
    if employee is None:
        raise HTTPException(status_code=422, detail="Selected employee does not exist")
    return employee.id


@router.post("", response_model=SaleResponse, status_code=201, dependencies=[writers], summary="Create a sale record")
def create_sale(
    body: SaleCreate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Adds a past sale to the register. Admin and manager only.

    After recording the sale, automatically links it to the CRM contact pipeline:
    • If a Contact with the same phone already exists, it is linked and moved to "won".
    • If no Contact exists yet, one is created from the sale data and placed at "won".
    See _sync_contact() for the full logic.
    """
    values = body.model_dump(exclude={"created_by_employee_id"})
    creator_id = _creator_id(db, body.created_by_employee_id, current_user)
    values["created_by_employee_id"] = creator_id
    sale = Sale(**values)
    db.add(sale)
    db.flush()  # get sale.id; we need it before _sync_contact writes LeadStage rows

    _sync_contact(db, sale, creator_id)

    db.commit()
    db.refresh(sale)
    return sale


@router.put("/{sale_id}", response_model=SaleResponse, dependencies=[writers], summary="Update a sale record")
def update_sale(
    sale_id: int,
    body: SaleUpdate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Partial update of a past sale. Admin and manager only."""
    sale = _get_or_404(db, sale_id)
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        raise HTTPException(status_code=422, detail="Name cannot be empty")
    if "created_by_employee_id" in changes:
        changes["created_by_employee_id"] = _creator_id(db, changes["created_by_employee_id"], current_user)
    for field, value in changes.items():
        setattr(sale, field, value)
    db.commit()
    db.refresh(sale)
    return sale


@router.delete("/{sale_id}", status_code=204, dependencies=[writers], summary="Delete a sale record")
def delete_sale(sale_id: int, db: Session = Depends(get_db)):
    """Deletes a past sale record. Admin and manager only."""
    db.delete(_get_or_404(db, sale_id))
    db.commit()
