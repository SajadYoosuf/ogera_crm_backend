"""
Expense management routes.

WHAT THIS FILE DOES:
    POST   /expenses                — staff submits an expense claim
    GET    /expenses                — list expenses (staff: own only; manager/admin: all)
    GET    /expenses/{id}           — single expense detail
    DELETE /expenses/{id}           — soft delete (creator only, pending status only)
    POST   /expenses/{id}/approve   — manager/admin approves a pending expense
    POST   /expenses/{id}/reject    — manager/admin rejects a pending expense and
                                      notifies the submitter (in-app + FCM push)
    POST   /expenses/{id}/bill      — upload/replace the receipt photo (creator only)

BUSINESS RULES:
    - Only staff (non-admin, non-manager) may CREATE expenses. Admin and manager
      oversee expenses, they do not submit them.
    - The amount is immutable: it is set at creation and this route never updates it.
    - Staff see only their own expenses. Manager and admin see all, with filters.
    - Only pending expenses may be deleted, and only by the creator.
    - Only pending expenses may be approved or rejected.
    - The bill photo is optional. It can be added or replaced at any time while
      the expense has not been deleted.
"""
import datetime
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from auth import get_current_user, HEAD_OFFICE_ROLES
from business_time import ist_now
from config import UPLOAD_DIR
from database import get_db
from models import Employee
from models.expense import Expense, EXPENSE_CATEGORIES, EXPENSE_STATUSES
from services.notification_service import create_notification

router = APIRouter(prefix="/expenses", tags=["expenses"], dependencies=[Depends(get_current_user)])

MAX_BILL_BYTES = 5 * 1024 * 1024  # 5 MB
BILL_TYPES = [
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n", "png"),
]

ExpenseCategory = Literal["petrol", "food", "maintenance", "accommodation", "other"]
ExpenseStatus = Literal["pending", "approved", "rejected"]


# ── Pydantic schemas ────────────────────────────────────────────────────────

class ExpenseCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    category: ExpenseCategory
    amount: Decimal = Field(..., gt=0, decimal_places=2, description="Amount in INR; immutable after creation")


class RejectBody(BaseModel):
    rejection_reason: str = Field(..., min_length=1, max_length=500)


class ExpenseResponse(BaseModel):
    id: int
    employee_id: int
    employee_name: str | None
    title: str
    category: str
    amount: Decimal
    bill_photo_url: str | None
    status: str
    rejection_reason: str | None
    reviewed_by: int | None
    reviewed_by_name: str | None
    reviewed_at: datetime.datetime | None
    created_at: datetime.datetime

    class Config:
        from_attributes = True


class ExpenseListResponse(BaseModel):
    items: list[ExpenseResponse]
    total: int
    pending_count: int


# ── Helpers ─────────────────────────────────────────────────────────────────

def _to_response(expense: Expense) -> ExpenseResponse:
    return ExpenseResponse(
        id=expense.id,
        employee_id=expense.employee_id,
        employee_name=expense.employee.name if expense.employee else None,
        title=expense.title,
        category=expense.category,
        amount=expense.amount,
        bill_photo_url=expense.bill_photo_url,
        status=expense.status,
        rejection_reason=expense.rejection_reason,
        reviewed_by=expense.reviewed_by,
        reviewed_by_name=expense.reviewer.name if expense.reviewer else None,
        reviewed_at=expense.reviewed_at,
        created_at=expense.created_at,
    )


def _get_expense(db: Session, expense_id: int, current_user: Employee) -> Expense:
    """Fetch the expense or raise 404. Staff can only see their own."""
    expense = (
        db.query(Expense)
        .options(joinedload(Expense.employee), joinedload(Expense.reviewer))
        .filter(Expense.id == expense_id, Expense.deleted_at.is_(None))
        .first()
    )
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    # Staff only see their own expenses.
    if current_user.role not in HEAD_OFFICE_ROLES and expense.employee_id != current_user.id:
        raise HTTPException(status_code=404, detail="Expense not found")
    return expense


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=ExpenseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit an expense claim",
)
def create_expense(
    body: ExpenseCreate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Creates a new expense claim with status = pending.
    Admin and manager cannot submit expenses — they approve them instead.
    """
    if current_user.role in HEAD_OFFICE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Managers and admins review expenses; they do not submit them",
        )
    expense = Expense(
        employee_id=current_user.id,
        title=body.title.strip(),
        category=body.category,
        amount=body.amount,  # written once here, never updated afterwards
    )
    db.add(expense)
    db.commit()
    db.refresh(expense)
    # Load relationships for the response
    db.refresh(expense)
    expense = (
        db.query(Expense)
        .options(joinedload(Expense.employee), joinedload(Expense.reviewer))
        .filter(Expense.id == expense.id)
        .first()
    )
    return _to_response(expense)


@router.get(
    "",
    response_model=ExpenseListResponse,
    summary="List expense claims",
)
def list_expenses(
    employee_id: int | None = None,
    expense_status: ExpenseStatus | None = Query(default=None, alias="status"),
    date_from: datetime.date | None = Query(default=None, description="Submitted on or after this date (YYYY-MM-DD)"),
    date_to: datetime.date | None = Query(default=None, description="Submitted on or before this date (YYYY-MM-DD)"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Staff: returns only their own expenses.
    Manager/admin: returns all expenses, with optional filters by employee_id, status
    and a date range (date_from / date_to, both inclusive).
    Also returns a pending_count so the manager badge stays accurate.
    """
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to")

    base_q = (
        db.query(Expense)
        .options(joinedload(Expense.employee), joinedload(Expense.reviewer))
        .filter(Expense.deleted_at.is_(None))
    )

    # Scope: staff see only their own records
    if current_user.role not in HEAD_OFFICE_ROLES:
        base_q = base_q.filter(Expense.employee_id == current_user.id)
    elif employee_id is not None:
        base_q = base_q.filter(Expense.employee_id == employee_id)

    if expense_status is not None:
        base_q = base_q.filter(Expense.status == expense_status)

    # Dates are whole days. "To" includes the full last day, so we compare
    # with the start of the NEXT day using "<" instead of "<= 23:59:59".
    if date_from is not None:
        base_q = base_q.filter(Expense.created_at >= datetime.datetime.combine(date_from, datetime.time.min))
    if date_to is not None:
        next_day = date_to + datetime.timedelta(days=1)
        base_q = base_q.filter(Expense.created_at < datetime.datetime.combine(next_day, datetime.time.min))

    total = base_q.count()
    # Pending count for the badge (ignores the status filter, scoped to the same employee set)
    pending_q = (
        db.query(Expense)
        .filter(Expense.deleted_at.is_(None), Expense.status == "pending")
    )
    if current_user.role not in HEAD_OFFICE_ROLES:
        pending_q = pending_q.filter(Expense.employee_id == current_user.id)
    pending_count = pending_q.count()

    items = (
        base_q
        .order_by(Expense.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return ExpenseListResponse(
        items=[_to_response(e) for e in items],
        total=total,
        pending_count=pending_count,
    )


@router.get(
    "/{expense_id}",
    response_model=ExpenseResponse,
    summary="Get an expense claim",
)
def get_expense(
    expense_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Returns one expense. Staff can only see their own."""
    return _to_response(_get_expense(db, expense_id, current_user))


@router.delete(
    "/{expense_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a pending expense",
)
def delete_expense(
    expense_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Soft-deletes the expense.
    Only the creator may delete it, and only while it is still pending.
    """
    expense = _get_expense(db, expense_id, current_user)
    if expense.employee_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own expenses")
    if expense.status != "pending":
        raise HTTPException(status_code=409, detail="Only pending expenses can be deleted")
    expense.deleted_at = ist_now()
    db.commit()


@router.post(
    "/{expense_id}/approve",
    response_model=ExpenseResponse,
    summary="Approve an expense claim",
)
def approve_expense(
    expense_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Approves the expense. Manager and admin only.
    Returns 409 when the expense is not pending.
    """
    if current_user.role not in HEAD_OFFICE_ROLES:
        raise HTTPException(status_code=403, detail="Only managers and admins may approve expenses")
    expense = _get_expense(db, expense_id, current_user)
    if expense.status != "pending":
        raise HTTPException(status_code=409, detail=f"Cannot approve a {expense.status} expense")
    expense.status = "approved"
    expense.reviewed_by = current_user.id
    expense.reviewed_at = ist_now()
    expense.rejection_reason = None
    db.commit()
    db.expire_all()
    return _to_response(_get_expense(db, expense_id, current_user))


@router.post(
    "/{expense_id}/reject",
    response_model=ExpenseResponse,
    summary="Reject an expense claim",
)
def reject_expense(
    expense_id: int,
    body: RejectBody,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Rejects the expense with a reason. Manager and admin only.
    Returns 409 when the expense is not pending.

    The submitter gets an in-app notification and an FCM push that carries the
    rejection note, so they know why the claim was refused.
    """
    if current_user.role not in HEAD_OFFICE_ROLES:
        raise HTTPException(status_code=403, detail="Only managers and admins may reject expenses")
    # min_length=1 lets "   " through, so check again after trimming.
    reason = body.rejection_reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="A rejection note is required")
    expense = _get_expense(db, expense_id, current_user)
    if expense.status != "pending":
        raise HTTPException(status_code=409, detail=f"Cannot reject a {expense.status} expense")
    expense.status = "rejected"
    expense.reviewed_by = current_user.id
    expense.reviewed_at = ist_now()
    expense.rejection_reason = reason
    # Added to the same session, so the notification row is saved by the commit below.
    create_notification(
        db,
        recipient_id=expense.employee_id,
        notif_type="expense_rejected",
        title="Expense claim rejected",
        message=(
            f'Your claim "{expense.title}" ({expense.amount:.2f} INR) was rejected. '
            f"Reason: {reason}"
        ),
        entity_type="expense",
        entity_id=expense.id,
    )
    db.commit()
    db.expire_all()
    return _to_response(_get_expense(db, expense_id, current_user))


@router.post(
    "/{expense_id}/bill",
    response_model=ExpenseResponse,
    summary="Upload or replace the receipt photo",
)
async def upload_bill(
    expense_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Attaches a JPEG or PNG receipt photo to the expense.
    Only the creator may upload / replace it.
    Accepts files up to 5 MB.
    """
    expense = _get_expense(db, expense_id, current_user)
    if expense.employee_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only upload bills for your own expenses")

    content = await file.read(MAX_BILL_BYTES + 1)
    if len(content) > MAX_BILL_BYTES:
        raise HTTPException(status_code=413, detail="Bill photo must be 5 MB or smaller")
    extension = next((ext for magic, ext in BILL_TYPES if content.startswith(magic)), None)
    if extension is None:
        raise HTTPException(status_code=422, detail="Bill photo must be a JPEG or PNG image")

    folder = Path(UPLOAD_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"bill-{uuid.uuid4().hex[:16]}.{extension}"
    (folder / name).write_bytes(content)

    # Remove the old bill file if one existed
    old_url = expense.bill_photo_url
    expense.bill_photo_url = f"/uploads/{name}"
    db.commit()
    if old_url and old_url.startswith("/uploads/bill-"):
        (folder / Path(old_url).name).unlink(missing_ok=True)

    db.expire_all()
    return _to_response(_get_expense(db, expense_id, current_user))
