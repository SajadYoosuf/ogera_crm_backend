"""Lead source CRUD endpoints.

WHAT IS A LEAD SOURCE?
A lead source records where a contact came from — e.g. "Field Visit", "WhatsApp",
"Referral". It is a dropdown option on the contact form and in the contacts filter.

ENDPOINT SUMMARY
  GET  /lead-sources        — active sources only, for the form dropdown (any employee)
  GET  /lead-sources/all    — every source including inactive, with usage counts (admin/manager)
  POST /lead-sources        — add a new source (admin/manager)
  PATCH /lead-sources/{id}  — rename or toggle active (admin/manager)
  DELETE /lead-sources/{id} — soft-delete if unused and not locked (admin/manager)

WHY SOFT-DELETE?
Soft-delete (is_active=0) means old contacts that reference this source still read
correctly in reports. A hard delete would leave those contacts with a broken FK.

LOCKED SOURCES
"field_visit" is locked — the mobile app captures GPS only for this source type.
Turning it off or removing it would break that feature.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import settings_catalog as catalog
from auth import get_current_user, get_tenant_db, require_admin_or_manager
from models import Contact, Employee, LeadSource
from schemas import LeadSourceSummary
from schemas.lead_source import (
    LeadSourceAdminResponse,
    LeadSourceCreateRequest,
    LeadSourceUpdateRequest,
)

router = APIRouter(prefix="/lead-sources", tags=["lead-sources"])

# Slugs the app logic branches on — cannot be turned off or removed.
_LOCKED = catalog.LOCKED.get(catalog.LEAD_SOURCE, set())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _usage(db: Session) -> dict[int, int]:
    """
    Returns a dict of {lead_source_id: active_contact_count}.
    Uses an outer join so sources with zero contacts still appear (count = 0).
    Only counts non-deleted contacts.
    """
    rows = (
        db.query(LeadSource.id, func.count(Contact.id))
        .outerjoin(
            Contact,
            (Contact.lead_source_id == LeadSource.id) & Contact.deleted_at.is_(None),
        )
        .group_by(LeadSource.id)
        .all()
    )
    return dict(rows)


def _to_admin(row: LeadSource, usage_count: int = 0) -> LeadSourceAdminResponse:
    """Converts a LeadSource ORM row into the admin response shape."""
    return LeadSourceAdminResponse(
        id=row.id,
        name=row.name,
        slug=row.slug,
        is_active=bool(row.is_active),
        locked=row.slug in _LOCKED,
        usage_count=usage_count,
    )


def _row_or_404(db: Session, source_id: int) -> LeadSource:
    row = db.query(LeadSource).filter(LeadSource.id == source_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lead source not found")
    return row


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A lead source with this name already exists")


# ---------------------------------------------------------------------------
# Read-only (any logged-in employee)
# ---------------------------------------------------------------------------

@router.get("", response_model=list[LeadSourceSummary], summary="Active lead sources (dropdown)")
def list_lead_sources(
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(get_current_user),
):
    """
    Returns only the active lead sources, ordered by name.
    Used to populate the Source dropdown on the contact form.
    Returns the minimal {id, name, slug} shape — no admin-only fields.
    """
    return (
        db.query(LeadSource)
        .filter(LeadSource.is_active == 1)
        .order_by(LeadSource.name)
        .all()
    )


# NOTE: define /all before /{source_id} so the literal path is matched first.
@router.get("/all", response_model=list[LeadSourceAdminResponse], summary="All lead sources (admin)")
def list_all_lead_sources(
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(require_admin_or_manager),
):
    """
    Returns every lead source including inactive ones, with how many contacts
    currently reference each one. Used by the admin Settings page.
    Admin and manager only.
    """
    rows = db.query(LeadSource).order_by(LeadSource.name).all()
    usage = _usage(db)
    return [_to_admin(r, usage.get(r.id, 0)) for r in rows]


# ---------------------------------------------------------------------------
# Admin CRUD (admin/manager only)
# ---------------------------------------------------------------------------

@router.post("", response_model=LeadSourceAdminResponse, status_code=201, summary="Create a lead source")
def create_lead_source(
    body: LeadSourceCreateRequest,
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(require_admin_or_manager),
):
    """
    Adds a new lead source option.

    The slug is auto-generated from the name so you only need to provide a
    human-readable label (e.g. "Social Media" → slug "social_media").

    If a deactivated source with the same slug already exists it is re-activated
    and renamed instead of creating a duplicate row.
    """
    slug = catalog.slugify(body.name, 40)

    existing = db.query(LeadSource).filter(LeadSource.slug == slug).first()
    if existing is not None:
        if existing.is_active:
            raise HTTPException(status_code=409, detail="A lead source with this name already exists")
        # re-activate a previously turned-off source
        existing.is_active = 1
        existing.name = body.name
        _commit(db)
        return _to_admin(existing, 0)

    row = LeadSource(name=body.name, slug=slug, is_active=1)
    db.add(row)
    _commit(db)
    return _to_admin(row, 0)


@router.patch("/{source_id}", response_model=LeadSourceAdminResponse, summary="Update a lead source")
def update_lead_source(
    source_id: int,
    body: LeadSourceUpdateRequest,
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(require_admin_or_manager),
):
    """
    Renames a lead source or toggles it active/inactive.

    The slug is never changed — it is the stable internal key that existing
    contact records already store. Only the display name changes.

    Locked sources (field_visit) cannot be turned off because the mobile
    app captures GPS only when this source is selected.
    """
    row = _row_or_404(db, source_id)
    data = body.model_dump(exclude_unset=True)

    if "is_active" in data and not data["is_active"] and row.slug in _LOCKED:
        raise HTTPException(
            status_code=400,
            detail="The app relies on this lead source; it can't be turned off",
        )

    if "name" in data:
        # check that no other row already has this name
        conflict = (
            db.query(LeadSource)
            .filter(LeadSource.name == data["name"], LeadSource.id != source_id)
            .first()
        )
        if conflict:
            raise HTTPException(status_code=409, detail="A lead source with this name already exists")
        row.name = data["name"]

    if "is_active" in data:
        row.is_active = int(data["is_active"])

    _commit(db)
    usage = _usage(db)
    return _to_admin(row, usage.get(row.id, 0))


@router.delete("/{source_id}", response_model=LeadSourceAdminResponse, summary="Delete a lead source")
def delete_lead_source(
    source_id: int,
    db: Session = Depends(get_tenant_db),
    _: Employee = Depends(require_admin_or_manager),
):
    """
    Soft-deletes a lead source (sets is_active=0 — the row is kept so old
    contact records still read correctly).

    Blocked if:
    - The source is locked (field_visit) — the app logic depends on it.
    - Any active contacts currently reference this source — deactivate it instead.
    """
    row = _row_or_404(db, source_id)

    if row.slug in _LOCKED:
        raise HTTPException(
            status_code=400,
            detail="The app relies on this lead source; it can't be removed",
        )

    usage = _usage(db)
    count = usage.get(row.id, 0)
    if count:
        raise HTTPException(
            status_code=409,
            detail=f"{count} contact(s) use this lead source. Deactivate it instead of deleting",
        )

    row.is_active = 0
    _commit(db)
    return _to_admin(row, 0)
