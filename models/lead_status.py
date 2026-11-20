"""
LeadStatus model — the pipeline stages a Lead can be in, before it is either
converted to a Contact or marked lost.

WHY A TABLE INSTEAD OF A HARDCODED LIST?
    Lead sources and branches already live in their own tables so the list can
    change without a code deploy. Lead statuses follow the same pattern:
    GET /leads/statuses reads this table, so the frontend's status dropdown
    (in the leads table and the lead detail modal) never hardcodes the list.

NOTE ON "converted":
    "converted" is NOT a row in this table. It is a special terminal status set
    only by POST /leads/{id}/convert (services.lead_service.convert_lead),
    never offered in the status dropdown.
"""
import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class LeadStatus(Base):
    __tablename__ = "lead_statuses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Stable key stored on leads.status (e.g. "attempt_to_contact"). Never change
    # an existing slug — leads already reference it by this string.
    slug: Mapped[str] = mapped_column(String(30), unique=True)
    # Human-readable label shown in the dropdown (e.g. "Attempt to contact").
    name: Mapped[str] = mapped_column(String(60))
    # Controls dropdown order (ascending). Does not need to be sequential.
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
