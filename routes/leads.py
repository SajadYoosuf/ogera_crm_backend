"""
Leads router — HTTP endpoints for the authenticated Leads module.

ROUTE OVERVIEW:
    GET    /leads                   — paginated list with filters
    POST   /leads                   — create a lead manually
    GET    /leads/stats             — counts by status and source (dashboard widget)
    GET    /leads/{id}              — single lead detail
    PUT    /leads/{id}              — update a lead (partial)
    DELETE /leads/{id}              — soft-delete a lead
    POST   /leads/{id}/convert      — convert lead to a Contact
    POST   /leads/{id}/send-whatsapp — manually trigger WhatsApp welcome message

AUTH:
    Every endpoint requires a valid JWT (Depends(get_current_user)).
    The tenant schema is extracted from the JWT (`current_user._tenant_schema`).
"""

import datetime
import json as _json
import logging
from datetime import timezone as _tz
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

import lead_export
import lead_import
from auth import get_current_user
from business_time import ist_day_start_utc
from database import get_db, get_session_for_schema
from models.employee import Employee
from models.lead import Lead
from models.lead_source import LeadSource
from schemas.lead import (
    BulkAssignRequest,
    BulkAssignResponse,
    ConvertLeadResponse,
    LeadCreate,
    LeadListResponse,
    LeadResponse,
    LeadStatsResponse,
    LeadStatusOption,
    LeadUpdate,
    WhatsAppSendResponse,
)
import services.lead_service as lead_svc
from services.lead_access import is_head, ensure_can_work_lead, ensure_is_head
from services.whatsapp_service import send_whatsapp_template

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/leads",
    tags=["leads"],
    dependencies=[Depends(get_current_user)],
)


@router.get("", response_model=LeadListResponse, summary="List leads")
def list_leads(
    status: str | None = Query(None, description="Filter by status: new/contacted/qualified/converted/lost"),
    source_id: int | None = Query(None, description="Filter by lead_sources.id (1=Walk-in, 3=Meta Ads, 4=Website, 5=WhatsApp…)"),
    assigned_to: int | None = Query(None, description="Filter by assigned employee id"),
    date_from: datetime.date | None = Query(None, description="Created on or after this date"),
    date_to: datetime.date | None = Query(None, description="Created on or before this date"),
    search: str | None = Query(None, description="Search by name, phone, or email"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: Employee = Depends(get_current_user),
):
    """
    Return a paginated list of leads for the current tenant.

    The list is sorted newest-first. Use `skip` and `limit` for pagination.
    """
    schema = current_user._tenant_schema
    try:
        with get_session_for_schema(schema) as db:
            return lead_svc.list_leads(
                db,
                current_employee=current_user,
                status=status,
                source_id=source_id,
                assigned_to=assigned_to,
                date_from=date_from,
                date_to=date_to,
                search=search,
                skip=skip,
                limit=limit,
            )
    except OperationalError as exc:
        logger.error("list_leads — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")


@router.get("/statuses", response_model=list[LeadStatusOption], summary="Valid lead statuses")
def list_lead_statuses(current_user: Employee = Depends(get_current_user)):
    """
    Returns the active rows of the lead_statuses table, in pipeline order.

    The frontend's status dropdown (in the leads table and the lead detail
    modal) calls this instead of hardcoding the list, so adding, renaming or
    reordering a status is a database change only — the frontend always shows
    whatever is active in lead_statuses.

    Note: "converted" is deliberately not one of these rows — it's a special
    terminal value set only by POST /leads/{id}/convert.
    """
    schema = current_user._tenant_schema
    try:
        with get_session_for_schema(schema) as db:
            return lead_svc.list_lead_statuses(db)
    except OperationalError as exc:
        logger.error("list_lead_statuses — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")


@router.get("/import/template", summary="Download the lead import template")
def import_template(_: Employee = Depends(get_current_user)):
    """A blank CSV with the recognised column headers, so staff see the exact names to use."""
    return Response(
        lead_import.template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="leads_import_template.csv"'},
    )


# IMPORTANT: these /import/* and /export routes MUST stay ABOVE /{lead_id} in this
# file. FastAPI matches in registration order; without that, "import"/"export"
# would match the int path param and return a 422 instead of this route.

@router.post("/import/analyze", summary="Dry-run a lead import file")
def import_analyze(
    file: UploadFile = File(...),
    default_source_id: int | None = Form(None),
    mapping: str | None = Form(None),
    current_user: Employee = Depends(get_current_user),
):
    """
    Read an uploaded CSV/XLSX, validate every row, and return a preview
    WITHOUT writing anything to the database. See lead_import.analyze().

    FORM FIELDS:
        file              — .csv or .xlsx upload
        default_source_id — lead source id to use when a row's source column
                             is blank or unrecognised
        mapping           — JSON {"0": "phone", "1": "first_name", ...}
                             (column index -> field name); omit to auto-detect
    """
    content = file.file.read()
    try:
        rows = lead_import.read_rows(file.filename or "", content)
    except lead_import.ImportFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if mapping:
        try:
            raw_map = _json.loads(mapping)
            col_map: dict[int, str] = {int(k): v for k, v in raw_map.items()}
            header_row = 0
        except Exception:
            raise HTTPException(status_code=422, detail="mapping must be a JSON object like {\"0\": \"phone\", \"1\": \"first_name\"}")
    else:
        header_row, col_map = lead_import.detect_header(rows)

    schema = current_user._tenant_schema
    try:
        with get_session_for_schema(schema) as db:
            options = lead_import.Options(default_source_id=default_source_id)
            results = lead_import.analyze(db, rows, header_row, col_map, options)
    except OperationalError as exc:
        logger.error("import_analyze — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")

    summary = lead_import.summarize(results)

    header_cells = rows[header_row] if header_row < len(rows) else []
    num_cols = max((len(r.raw) for r in results), default=len(header_cells))
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
            "field": col_map.get(col_idx),
            "sample": samples.get(col_idx, []),
        }
        for col_idx in range(num_cols)
    ]

    required = {"phone"}
    missing_required = [f for f in required if f not in set(col_map.values())]

    problem_rows = [
        {
            "row": r.row,
            "status": r.status,
            "issues": r.issues,
            "raw": r.raw,
            "name": r.data.get("first_name"),
            "phone": r.data.get("phone"),
        }
        for r in results
        if r.status != "ready"
    ]

    return {
        "header_row": header_row,
        "columns": columns,
        "fields": dict(lead_import.FIELDS),
        "missing_required": missing_required,
        "summary": summary,
        "rows": problem_rows,
    }


@router.post("/import/commit", summary="Write a lead import to the database")
def import_commit(
    file: UploadFile = File(...),
    default_source_id: int | None = Form(None),
    mapping: str = Form(...),
    assign_mode: str = Form("unassigned"),
    assigned_to: int | None = Form(None),
    current_user: Employee = Depends(get_current_user),
):
    """
    Re-analyze the same file (so the preview and the actual import always
    agree) and write every ready/warning row as a new lead.

    FORM FIELDS:
        file              — same .csv or .xlsx as analyze
        default_source_id — fallback lead source id
        mapping           — required JSON column->field map from the analyze response
        assign_mode       — "auto" | "unassigned" | "specific"
        assigned_to       — employee id, required when assign_mode="specific"
    """
    if assign_mode not in ("auto", "unassigned", "specific"):
        raise HTTPException(status_code=422, detail="assign_mode must be 'auto', 'unassigned' or 'specific'")
    if assign_mode == "specific" and assigned_to is None:
        raise HTTPException(status_code=422, detail="assigned_to is required when assign_mode is 'specific'")

    content = file.file.read()
    try:
        rows = lead_import.read_rows(file.filename or "", content)
    except lead_import.ImportFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        raw_map = _json.loads(mapping)
        col_map: dict[int, str] = {int(k): v for k, v in raw_map.items()}
    except Exception:
        raise HTTPException(status_code=422, detail="mapping must be a JSON object like {\"0\": \"phone\", \"1\": \"first_name\"}")

    schema = current_user._tenant_schema
    try:
        with get_session_for_schema(schema) as db:
            options = lead_import.Options(default_source_id=default_source_id)
            results = lead_import.analyze(db, rows, 0, col_map, options)
            result = lead_import.run_import(
                db, results,
                created_by=current_user.id,
                assign_mode=assign_mode,
                specific_employee_id=assigned_to,
            )
    except OperationalError as exc:
        logger.error("import_commit — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")
    return result


@router.get("/export", summary="Export leads")
def export_leads(
    format: str = Query("xlsx", pattern="^(csv|xlsx)$"),
    status: str | None = Query(None),
    source_id: int | None = Query(None),
    assigned_to: int | None = Query(None),
    date_from: datetime.date | None = Query(None),
    date_to: datetime.date | None = Query(None),
    search: str | None = Query(None),
    current_user: Employee = Depends(get_current_user),
):
    """Downloads the leads matching the list filters as CSV or Excel — same filters as GET /leads."""
    schema = current_user._tenant_schema
    try:
        with get_session_for_schema(schema) as db:
            q = db.query(Lead).options(joinedload(Lead.creator), joinedload(Lead.branch)).filter(Lead.deleted_at.is_(None))
            if status:
                q = q.filter(Lead.status == status)
            if source_id:
                q = q.filter(Lead.source_id == source_id)
            if assigned_to:
                q = q.filter(Lead.assigned_to == assigned_to)
            if date_from:
                q = q.filter(Lead.created_at >= ist_day_start_utc(date_from))
            if date_to:
                q = q.filter(Lead.created_at < ist_day_start_utc(date_to + datetime.timedelta(days=1)))
            if search:
                term = f"%{search}%"
                from sqlalchemy import or_
                q = q.filter(or_(Lead.first_name.ilike(term), Lead.last_name.ilike(term), Lead.phone.ilike(term)))

            total = q.count()
            limit = lead_export.MAX_ROWS[format]
            if total > limit:
                raise HTTPException(status_code=400, detail=f"{total} leads match; a {format.upper()} export holds at most {limit}. Narrow the filters.")

            leads = q.order_by(Lead.created_at.desc()).all()

            emp_ids = {l.assigned_to for l in leads if l.assigned_to}
            employee_names = {}
            if emp_ids:
                employee_names = dict(db.query(Employee.id, Employee.name).filter(Employee.id.in_(emp_ids)).all())
            src_ids = {l.source_id for l in leads if l.source_id}
            source_names = {}
            if src_ids:
                source_names = dict(db.query(LeadSource.id, LeadSource.name).filter(LeadSource.id.in_(src_ids)).all())

            rows = lead_export.build_rows(leads, employee_names, source_names)
            body = lead_export.to_csv(rows) if format == "csv" else lead_export.to_xlsx(rows)
    except OperationalError as exc:
        logger.error("export_leads — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")

    filename = f"leads_{datetime.date.today():%Y%m%d}.{format}"
    return Response(
        body,
        media_type=lead_export.MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/stats", response_model=LeadStatsResponse, summary="Lead counts by status and source")
def lead_stats(current_user: Employee = Depends(get_current_user)):
    """
    Return lead counts grouped by status and source.
    Used by the dashboard to show a mini lead summary widget.
    """
    schema = current_user._tenant_schema
    try:
        with get_session_for_schema(schema) as db:
            return lead_svc.get_lead_stats(db)
    except OperationalError as exc:
        logger.error("lead_stats — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")


@router.get("/{lead_id}", response_model=LeadResponse, summary="Get one lead")
def get_lead(lead_id: int, current_user: Employee = Depends(get_current_user)):
    """
    Return full detail for a single lead (includes source_name, created_by_name, branch_name).

    RESPONSE CODES:
      200 — found
      404 — no lead with this id, or it was soft-deleted (raised by get_lead_detail)
      422 — lead_id in the URL is not a whole number (FastAPI validates this
            automatically from the `int` type hint, before this function runs)
      503 — the database is unreachable or the query failed
    """
    schema = current_user._tenant_schema
    try:
        with get_session_for_schema(schema) as db:
            lead = lead_svc.get_lead(db, lead_id)
            ensure_can_work_lead(current_user, lead)
            return lead_svc.get_lead_detail(db, lead_id)
    except OperationalError as exc:
        logger.error("get_lead — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")


@router.post("", response_model=LeadResponse, status_code=201, summary="Create a lead manually")
def create_lead(
    body: LeadCreate,
    background_tasks: BackgroundTasks,
    allow_duplicate: bool = Query(False, description="Set true to skip duplicate check"),
    current_user: Employee = Depends(get_current_user),
):
    """
    Create a new lead manually (by a staff member).

    Only the phone number is required — email is not collected at creation.
    If the phone already exists in leads or contacts, a 409 is returned with the
    existing record's id. Set `?allow_duplicate=true` to override (admin/manager only).

    branch_id is optional: omit it to default to the creating employee's own
    branch, or send a different id to pick another branch.

    A WhatsApp welcome message is sent automatically in the background if phone is present.
    """
    if allow_duplicate and not is_head(current_user):
        raise HTTPException(status_code=403, detail="Only admin or manager can bypass duplicate check")
    if body.created_at is not None and not is_head(current_user):
        raise HTTPException(status_code=403, detail="Only admin or manager can backdate leads")
    # Staff cannot choose the assignee; heads can. Auto-assign runs when assigned_to is None.
    if body.assigned_to is not None and not is_head(current_user):
        raise HTTPException(status_code=403, detail="Only admin or manager can assign leads to a specific person")
    schema = current_user._tenant_schema
    try:
        with get_session_for_schema(schema) as db:
            return lead_svc.create_lead(
                db=db,
                schema=schema,
                first_name=body.first_name,
                source_id=body.source_id,
                last_name=body.last_name,
                phone=body.phone,
                notes=body.notes,
                address=body.address,
                tags=body.tags,
                assigned_to=body.assigned_to,
                branch_id=body.branch_id,
                allow_duplicate=allow_duplicate,
                background_tasks=background_tasks,
                created_at=body.created_at,
                # Taken from the login token, never from the request body,
                # so a client cannot claim someone else created the lead.
                created_by=current_user.id,
            )
    except OperationalError as exc:
        logger.error("create_lead — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")


@router.put("/{lead_id}", response_model=LeadResponse, summary="Update a lead")
def update_lead(
    lead_id: int,
    body: LeadUpdate,
    current_user: Employee = Depends(get_current_user),
):
    """
    Partially update a lead. Only the fields you send are changed.

    Example: send {"status": "contacted"} to move the lead to 'contacted' status
    without touching any other fields.

    status must be one of the active lead_statuses slugs (see GET /leads/statuses),
    or "converted" — the terminal value normally set by POST /leads/{id}/convert,
    accepted here too so a mistaken conversion can be reverted by hand.
    """
    schema = current_user._tenant_schema
    try:
        with get_session_for_schema(schema) as db:
            lead = lead_svc.get_lead(db, lead_id)
            ensure_can_work_lead(current_user, lead)
            if body.status is not None:
                lead_svc.ensure_valid_status(db, body.status)
            # Only heads can reassign leads
            updates = body.model_dump(exclude_none=True)
            if "assigned_to" in updates and not is_head(current_user):
                raise HTTPException(status_code=403, detail="Only admin or manager can reassign leads")
            previous_assignee = lead.assigned_to
            updated = lead_svc.update_lead(db, lead_id, actor_id=current_user.id, **updates)
            if updated.assigned_to != previous_assignee:
                lead_svc.notify_lead_assigned(db, updated, current_user.id)
                db.commit()
            return updated
    except OperationalError as exc:
        logger.error("update_lead — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")


@router.delete("/{lead_id}", status_code=204, summary="Soft-delete a lead")
def delete_lead(lead_id: int, current_user: Employee = Depends(get_current_user)):
    """
    Soft-delete a lead (sets deleted_at, never hard-deletes).
    The lead is no longer shown in any list but its data is preserved.
    """
    ensure_is_head(current_user, "delete leads")
    schema = current_user._tenant_schema
    try:
        with get_session_for_schema(schema) as db:
            lead_svc.soft_delete_lead(db, lead_id)
    except OperationalError as exc:
        logger.error("delete_lead — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")


@router.post("/{lead_id}/convert", response_model=ConvertLeadResponse, summary="Convert lead to Contact")
def convert_lead(lead_id: int, current_user: Employee = Depends(get_current_user)):
    """
    Convert a lead into a full Contact.

    If a contact with the same phone number already exists, the lead is linked to
    that contact instead of creating a duplicate.

    After conversion, navigate the user to /contacts/{contact_id}.
    """
    schema = current_user._tenant_schema
    try:
        with get_session_for_schema(schema) as db:
            lead = lead_svc.get_lead(db, lead_id)
            ensure_can_work_lead(current_user, lead)
            result = lead_svc.convert_lead(db, lead_id, current_user.id)
            return result
    except OperationalError as exc:
        logger.error("convert_lead — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")


@router.post("/{lead_id}/send-whatsapp", response_model=WhatsAppSendResponse, summary="Manually send WhatsApp message")
def send_whatsapp(lead_id: int, current_user: Employee = Depends(get_current_user)):
    """
    Manually trigger the WhatsApp welcome template for a lead.

    Useful when the automatic send failed (e.g. the API was temporarily down)
    or when a staff member wants to re-send after updating the phone number.
    """
    schema = current_user._tenant_schema
    try:
        with get_session_for_schema(schema) as db:
            lead = lead_svc.get_lead(db, lead_id)
            if not lead.phone:
                raise HTTPException(status_code=400, detail="Lead has no phone number")
            sent = send_whatsapp_template(lead.phone, "welcome_lead", [lead.first_name])
            if sent:
                lead.whatsapp_sent = True
                lead.whatsapp_sent_at = datetime.datetime.now(_tz.utc)
                try:
                    db.commit()
                except SQLAlchemyError as exc:
                    db.rollback()
                    logger.error("send_whatsapp — commit failed: %s", exc)
                    raise HTTPException(503, detail="Could not save. Please try again.")
            return {"success": sent}
    except OperationalError as exc:
        logger.error("send_whatsapp — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")


@router.post("/bulk-assign", response_model=BulkAssignResponse, summary="Bulk assign unassigned leads by source")
def bulk_assign_leads(
    body: BulkAssignRequest,
    current_user: Employee = Depends(get_current_user),
):
    """
    Assign batches of unassigned leads to employees.

    BODY EXAMPLE:
        {
          "assignments": [
            { "source_id": 3, "assigned_to": 5, "count": 15 },
            { "source_id": 3, "assigned_to": 7, "count": 15 },
            { "source_id": 5, "assigned_to": 5, "count": 20 }
          ]
        }

    Each rule picks the N oldest unassigned leads from that source and assigns
    them to the given employee. source_id=null means "any source".
    Returns the total number of leads actually assigned (may be less than
    requested if fewer unassigned leads exist for that source).
    """
    schema = current_user._tenant_schema
    try:
        with get_session_for_schema(schema) as db:
            assigned = lead_svc.bulk_assign_leads(db, [r.model_dump() for r in body.assignments])
    except OperationalError as exc:
        logger.error("bulk_assign_leads — DB error: %s", exc)
        raise HTTPException(503, detail="Server unavailable, please retry in a moment.")
    return BulkAssignResponse(assigned=assigned)
