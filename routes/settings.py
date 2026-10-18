import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import settings_catalog as catalog
from auth import get_current_user, require_roles
from config import UPLOAD_DIR
from role_access import ACTION_ROLES
from database import get_db
from models import Employee, EnumValue, LeadSource, Role, RolePermission, StageType
from schemas.settings import (
    CompanyResponse,
    CompanyUpdate,
    EnumReorder,
    EnumValueCreate,
    EnumValueResponse,
    EnumValueUpdate,
    PermissionChange,
    PermissionCell,
    PermissionFeature,
    PermissionMatrix,
    PermissionRole,
)

router = APIRouter(prefix="/settings", tags=["settings"])

require_admin = require_roles("admin")
require_manager_or_admin = require_roles("admin", "manager")

MAX_LOGO_BYTES = 2 * 1024 * 1024
LOGO_TYPES = (  # (magic bytes, extension); SVG is left out because it can carry scripts
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
)

# Table + column names behind each category (lead sources and stages live in their own tables).
MODELS = {catalog.LEAD_SOURCE: LeadSource, catalog.LEAD_STAGE: StageType}
COLUMNS = {
    catalog.LEAD_SOURCE: ("name", "slug", None),  # sources are listed by name, no manual order
    catalog.LEAD_STAGE: ("name", "slug", "order"),
}
DEFAULT_COLUMNS = ("label", "value", "sort_order")


def _category_or_404(category: str) -> str:
    if category not in catalog.CATEGORIES:
        raise HTTPException(status_code=404, detail="Unknown category")
    return category


def _model(category: str):
    return MODELS.get(category, EnumValue)


def _cols(category: str) -> tuple[str, str, str | None]:
    return COLUMNS.get(category, DEFAULT_COLUMNS)


def _row_or_404(db: Session, category: str, item_id: int):
    model = _model(category)
    query = db.query(model).filter(model.id == item_id)
    if model is EnumValue:
        query = query.filter(EnumValue.category == category)
    row = query.first()
    if not row:
        raise HTTPException(status_code=404, detail="Value not found")
    return row


def _response(db: Session, category: str, item_id: int) -> EnumValueResponse:
    for item in catalog.list_items(db, category, with_usage=True):
        if item.id == item_id:
            return EnumValueResponse(**item.__dict__)
    raise HTTPException(status_code=404, detail="Value not found")


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An option with this name already exists")


# ---- Enum values ----------------------------------------------------------------------------------

@router.get("/enums", response_model=dict[str, list[EnumValueResponse]], summary="All dropdown options (admin)")
def list_all_enums(db: Session = Depends(get_db), _: Employee = Depends(require_manager_or_admin)):
    """Every category with all its values, including inactive ones, and how many records use each value. Admin and manager."""
    return {
        category: [EnumValueResponse(**i.__dict__) for i in catalog.list_items(db, category, with_usage=True)]
        for category in catalog.CATEGORIES
    }


@router.get("/enums/{category}", response_model=list[EnumValueResponse], summary="Dropdown options of one category")
def list_enum(category: str, db: Session = Depends(get_db), _: Employee = Depends(get_current_user)):
    """Active options of one category, in order, for dropdowns. Open to every signed-in user."""
    _category_or_404(category)
    # Dropdowns get the consumer pipeline only, as before; the other pipelines are managed in Settings.
    return [
        EnumValueResponse(**i.__dict__)
        for i in catalog.list_items(db, category)
        if i.is_active and (category != catalog.LEAD_STAGE or i.group == "consumer")
    ]


PIPELINES = ("consumer", "partner", "dealer")


def _create_stage(db: Session, body: EnumValueCreate) -> EnumValueResponse:
    """Adds a stage to one contact type's pipeline, at the end. A stage turned off earlier comes back instead."""
    pipeline = body.group or "consumer"
    if pipeline not in PIPELINES:
        raise HTTPException(status_code=422, detail="Choose consumer, partner or dealer")
    if len(body.label) > 60:
        raise HTTPException(status_code=422, detail="Name can be at most 60 characters")
    slug = catalog.slugify(body.value or body.label, 40)

    existing = db.query(StageType).filter(StageType.slug == slug, StageType.contact_type == pipeline).first()
    if existing is not None:
        if existing.is_active:
            raise HTTPException(status_code=409, detail="This stage already exists in this pipeline")
        existing.is_active = 1
        existing.name = body.label
        _commit(db)
        return _response(db, catalog.LEAD_STAGE, existing.id)

    last = max(
        (r.order for r in db.query(StageType).filter(StageType.contact_type == pipeline, StageType.is_active == 1).all()),
        default=0,
    )
    row = StageType(name=body.label, slug=slug, contact_type=pipeline, description=None, order=last + 1, is_active=1)
    db.add(row)
    _commit(db)
    return _response(db, catalog.LEAD_STAGE, row.id)


@router.post("/enums/{category}", response_model=EnumValueResponse, status_code=201, summary="Add a dropdown option")
def create_enum(category: str, body: EnumValueCreate, db: Session = Depends(get_db), _: Employee = Depends(require_manager_or_admin)):
    """Adds a value to a category (admin and manager). The stored value cannot be changed later, so pick it carefully.
    Lead stages also take `group` (consumer, partner or dealer): the pipeline the stage joins."""
    _category_or_404(category)
    if category == catalog.LEAD_STAGE:
        return _create_stage(db, body)
    label_col, value_col, order_col = _cols(category)
    max_label = 60 if category == catalog.LEAD_SOURCE else 100
    if len(body.label) > max_label:
        raise HTTPException(status_code=422, detail=f"Name can be at most {max_label} characters")
    value = catalog.slugify(body.value or body.label, 40 if category == catalog.LEAD_SOURCE else 100)

    model = _model(category)
    existing = db.query(model).filter(getattr(model, value_col) == value)
    if model is EnumValue:
        existing = existing.filter(EnumValue.category == category)
    existing = existing.first()
    if existing is not None:
        if existing.is_active:
            raise HTTPException(status_code=409, detail="This option already exists")
        existing.is_active = 1  # adding a turned-off option again brings the old one back
        setattr(existing, label_col, body.label)
        _commit(db)
        return _response(db, category, existing.id)

    last = max((i.sort_order for i in catalog.list_items(db, category) if i.is_active), default=0)
    fields = {label_col: body.label, value_col: value}
    if order_col:
        fields[order_col] = last + 1
    row = model(**fields, **({"category": category} if model is EnumValue else {}))
    db.add(row)
    _commit(db)
    return _response(db, category, row.id)


@router.patch("/enums/{category}/{item_id}", response_model=EnumValueResponse, summary="Update a dropdown option")
def update_enum(category: str, item_id: int, body: EnumValueUpdate, db: Session = Depends(get_db), _: Employee = Depends(require_manager_or_admin)):
    """Renames, repositions or switches an option on or off (admin and manager). The stored value itself never changes."""
    _category_or_404(category)
    row = _row_or_404(db, category, item_id)
    label_col, value_col, order_col = _cols(category)
    data = body.model_dump(exclude_unset=True)
    for field in ("label", "is_active", "sort_order"):
        if field in data and data[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null")
    if data.get("is_active") is False and getattr(row, value_col) in catalog.LOCKED.get(category, ()):
        raise HTTPException(status_code=400, detail="The app relies on this option, so it can't be turned off")
    if "label" in data:
        if category == catalog.LEAD_SOURCE and len(data["label"]) > 60:
            raise HTTPException(status_code=422, detail="Name can be at most 60 characters")
        setattr(row, label_col, data["label"])
    if "sort_order" in data and order_col:
        setattr(row, order_col, data["sort_order"])
    if "is_active" in data:
        row.is_active = int(data["is_active"])
    _commit(db)
    return _response(db, category, item_id)


@router.delete("/enums/{category}/{item_id}", response_model=EnumValueResponse, summary="Delete a dropdown option")
def delete_enum(category: str, item_id: int, db: Session = Depends(get_db), _: Employee = Depends(require_manager_or_admin)):
    """Removes an unused option (admin and manager). The option is turned off rather than erased, so old reports still read correctly. Options in use must be deactivated instead."""
    _category_or_404(category)
    row = _row_or_404(db, category, item_id)
    value = getattr(row, _cols(category)[1])
    if value in catalog.LOCKED.get(category, ()):
        raise HTTPException(status_code=400, detail="The app relies on this option, so it can't be removed")
    used = catalog.usage_counts(db, category).get(value.lower(), 0)
    if used:
        raise HTTPException(status_code=400, detail=f"{used} record(s) use this option. Deactivate it instead")
    row.is_active = 0
    _commit(db)
    return _response(db, category, item_id)


@router.post("/enums/{category}/reorder", response_model=list[EnumValueResponse], summary="Reorder dropdown options")
def reorder_enum(category: str, body: EnumReorder, db: Session = Depends(get_db), _: Employee = Depends(require_manager_or_admin)):
    """Sets the order of a category to the position of each id in `ordered_ids` (admin and manager)."""
    _category_or_404(category)
    if len(set(body.ordered_ids)) != len(body.ordered_ids):
        raise HTTPException(status_code=422, detail="ordered_ids has duplicates")
    model = _model(category)
    order_col = _cols(category)[2]
    query = db.query(model).filter(model.id.in_(body.ordered_ids))
    if model is EnumValue:
        query = query.filter(EnumValue.category == category)
    rows = {r.id: r for r in query.all()}
    if len(rows) != len(body.ordered_ids):
        raise HTTPException(status_code=400, detail="ordered_ids contains an id that is not in this category")
    for position, item_id in enumerate(body.ordered_ids, start=1):
        setattr(rows[item_id], order_col, position)
    _commit(db)
    return [EnumValueResponse(**i.__dict__) for i in catalog.list_items(db, category, with_usage=True)]


# ---- Company ---------------------------------------------------------------------------------------

def _company(db: Session) -> CompanyResponse:
    return CompanyResponse(**catalog.get_company(db))


@router.get("/company", response_model=CompanyResponse, summary="Get company profile")
def get_company(db: Session = Depends(get_db), _: Employee = Depends(require_manager_or_admin)):
    """The company name and logo. Admin and manager only."""
    return _company(db)


@router.patch("/company", response_model=CompanyResponse, summary="Update company profile")
def update_company(body: CompanyUpdate, db: Session = Depends(get_db), _: Employee = Depends(require_admin)):
    """Changes the company name (admin only)."""
    data = body.model_dump(exclude_unset=True)
    if data.get("company_name") is None:
        data.pop("company_name", None)
    if "company_logo_url" in data:
        url = (data["company_logo_url"] or "").strip()
        if url and not (url.startswith("/uploads/") or url.startswith(("http://", "https://"))):
            raise HTTPException(status_code=422, detail="Logo address must be an uploaded file or an http(s) link")
        data["company_logo_url"] = url or None
    if data:
        catalog.set_company(db, data)
    return _company(db)


@router.post("/company/logo", response_model=CompanyResponse, summary="Upload company logo")
async def upload_logo(file: UploadFile = File(...), db: Session = Depends(get_db), _: Employee = Depends(require_admin)):
    """Stores a PNG or JPEG logo of up to 2 MB and makes it the company logo (admin only)."""
    content = await file.read(MAX_LOGO_BYTES + 1)
    if len(content) > MAX_LOGO_BYTES:
        raise HTTPException(status_code=413, detail="Logo must be 2 MB or smaller")
    extension = next((ext for magic, ext in LOGO_TYPES if content.startswith(magic)), None)
    if extension is None:
        raise HTTPException(status_code=422, detail="Logo must be a PNG or JPEG image")

    folder = Path(UPLOAD_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"logo-{uuid.uuid4().hex[:12]}.{extension}"
    (folder / name).write_bytes(content)

    previous = catalog.get_company(db)["company_logo_url"]
    catalog.set_company(db, {"company_logo_url": f"/uploads/{name}"})
    if previous and previous.startswith("/uploads/logo-"):
        (folder / Path(previous).name).unlink(missing_ok=True)
    return _company(db)


# ---- Roles & permissions ----------------------------------------------------------------------------

def _cell(level: str, note: str | None = None) -> dict:
    return {"level": level, "note": note}


# What each kind of role can do by default. Admin and manager are team-wide roles; every other role
# (built-in or custom) works with its own records. Rows with an action can be switched on or off per role;
# the others (employees, settings) are fixed in code.
# Each row: (key, label, description, action, admin cell, manager cell, staff cell).
FEATURES = [
    ("contacts", "Contacts", "View and edit the contacts they can see", None,
     _cell("full"), _cell("full"), _cell("own", "Only contacts they created or are assigned to")),
    ("add_contacts", "Add consumer contacts", "Create new consumer contacts", "create_contact",
     _cell("full"), _cell("full"), _cell("full")),
    ("partners", "Add partners & dealers", "Create partner and dealer contacts", "create_partner_or_dealer",
     _cell("full"), _cell("full"), _cell("full")),
    ("followups", "Lead Followup board", "Move leads through the pipeline", "move_stage",
     _cell("full"), _cell("full"), _cell("none")),
    ("tasks", "Tasks", "Schedule follow-ups and tasks", "schedule_followup",
     _cell("full"), _cell("full"), _cell("own", "Only tasks assigned to them")),
    ("activity", "Activity log", "Log calls, visits and notes on a lead", "log_activity",
     _cell("full"), _cell("full"), _cell("own", "Only contacts they can see")),
    ("export", "Export contacts", "Download contacts as CSV, Excel or PDF", "export",
     _cell("full"), _cell("full"), _cell("none")),
    ("employees", "Employees", "Add staff and manage logins", None,
     _cell("full"), _cell("full", "Cannot change admin or manager accounts"), _cell("none")),
    ("settings", "Settings", "Company, dropdown options and roles", None,
     _cell("full"), _cell("view", "Company and roles only"), _cell("none")),
]

# Per-role levels that differ from the defaults above. Keys are (feature key, role slug).
FEATURE_OVERRIDES = {
    ("contacts", "team_lead"): _cell("team", "Their own contacts and their team's"),
    ("contacts", "support"): _cell("customers", "Their own contacts and customers (won, placed, first order)"),
    ("followups", "team_lead"): _cell("team", "Their team's follow-ups; schedules within the team"),
    ("followups", "sales_executive"): _cell("own", "Follow-ups on their own leads"),
    ("followups", "tele_caller"): _cell("own", "Follow-ups on their own leads"),
    ("followups", "field_executive"): _cell("own", "Early stages only: New Lead, Aware, Connected"),
    ("tasks", "team_lead"): _cell("team", "Their team's tasks"),
    ("tasks", "support"): _cell("own", "Only tasks assigned to them; cannot schedule for others"),
    ("activity", "team_lead"): _cell("team", "Their team's contacts"),
    ("export", "team_lead"): _cell("team", "Their team's contacts"),
}


def _may_edit(viewer: Employee, slug: str) -> bool:
    """Admin never loses access. Managers can change staff and custom roles; only an admin changes managers."""
    if slug == "admin":
        return False
    if slug == "manager":
        return viewer.role == "admin"
    return True


def _allowed(saved: dict[tuple[str, str], bool], slug: str, action: str) -> bool:
    if slug == "admin":
        return True
    if (slug, action) in saved:
        return saved[(slug, action)]
    return slug in ACTION_ROLES[action]


def _build_matrix(roles: list[Role], saved: dict[tuple[str, str], bool], viewer: Employee) -> PermissionMatrix:
    features = []
    for key, label, description, action, admin, manager, staff in FEATURES:
        levels = {}
        for role in roles:
            default = admin if role.slug == "admin" else manager if role.slug == "manager" else staff
            cell = FEATURE_OVERRIDES.get((key, role.slug), default)
            editable = False
            if action:
                explicit = (role.slug, action) in saved
                if not _allowed(saved, role.slug, action):
                    cell = _cell("none", "Turned off in Settings" if explicit else None)
                elif cell["level"] == "none":
                    cell = _cell("full", "Turned on in Settings" if explicit else None)
                editable = _may_edit(viewer, role.slug)
            levels[role.slug] = PermissionCell(level=cell["level"], note=cell["note"], editable=editable)
        features.append(PermissionFeature(key=key, label=label, description=description, action=action, levels=levels))
    return PermissionMatrix(
        roles=[PermissionRole(slug=r.slug, name=r.name, is_system=bool(r.is_system), is_active=bool(r.is_active)) for r in roles],
        features=features,
    )


@router.get("/roles", response_model=PermissionMatrix, summary="Permission matrix")
def permission_matrix(db: Session = Depends(get_db), viewer: Employee = Depends(require_manager_or_admin)):
    """Access level of every role for each feature, with the cells this viewer may switch on or off. Admin and manager only."""
    roles = db.query(Role).order_by(Role.is_system.desc(), Role.name).all()
    saved = {(p.role_slug, p.action): bool(p.allowed) for p in db.query(RolePermission).all()}
    return _build_matrix(roles, saved, viewer)


@router.put("/roles/{slug}/permissions/{action}", response_model=PermissionMatrix, summary="Allow or block an action for a role")
def set_role_permission(
    slug: str,
    action: str,
    body: PermissionChange,
    db: Session = Depends(get_db),
    viewer: Employee = Depends(require_manager_or_admin),
):
    """Turns one action on or off for one role and returns the updated matrix. Admin keeps every permission;
    only an admin can change what managers may do. Staff roles can be changed by admin and manager."""
    if action not in ACTION_ROLES:
        raise HTTPException(status_code=404, detail="Unknown action")
    role = db.query(Role).filter(Role.slug == slug).first()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    if not _may_edit(viewer, slug):
        detail = "Admin always keeps every permission" if slug == "admin" else "Only an admin can change what managers may do"
        raise HTTPException(status_code=403, detail=detail)

    row = db.query(RolePermission).filter(RolePermission.role_slug == slug, RolePermission.action == action).first()
    if row is None:
        row = RolePermission(role_slug=slug, action=action)
        db.add(row)
    row.allowed = int(body.allowed)
    db.commit()
    return permission_matrix(db=db, viewer=viewer)
