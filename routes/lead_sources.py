from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from auth import get_current_user
from database import get_db
from models import Employee, LeadSource
from schemas import LeadSourceSummary

router = APIRouter(prefix="/lead-sources", tags=["lead-sources"])


@router.get("", response_model=list[LeadSourceSummary], summary="List lead sources")
def list_lead_sources(
    db: Session = Depends(get_db),
    _: Employee = Depends(get_current_user),
):
    """Active lead sources for the Source dropdown on the contact form."""
    return db.query(LeadSource).filter(LeadSource.is_active == 1).order_by(LeadSource.name).all()
