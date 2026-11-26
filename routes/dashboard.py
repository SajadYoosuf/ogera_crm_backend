"""
Dashboard router.

ENDPOINTS:
    GET /dashboard/summary?period=today|7d|30d|custom&date_from=&date_to=&assigned_to=

ACCESS:
    Any signed-in employee. The view and the data are chosen from the employee's role: admin and manager see
    everyone (or one employee with assigned_to); every other role sees only their own work, and assigned_to
    is ignored for them. The rules live in services/dashboard_service.py.
"""
import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from auth import get_current_user, get_tenant_db
from models.employee import Employee
from services.dashboard_service import build_summary, resolve_period

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", summary="The signed-in employee's dashboard")
def dashboard_summary(
    period: Literal["today", "7d", "30d", "custom"] = Query("30d"),
    date_from: datetime.date | None = Query(None, description="IST date, with period=custom"),
    date_to: datetime.date | None = Query(None, description="IST date, with period=custom"),
    assigned_to: int | None = Query(None, description="Admin/manager only; ignored for staff"),
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(get_current_user),
):
    return build_summary(db, current_user, resolve_period(period, date_from, date_to), assigned_to)
