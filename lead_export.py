"""
Lead export to CSV and Excel.

Reuses contact_export.to_csv / to_xlsx directly — both just turn a plain
list-of-rows + headers into bytes, nothing contact-specific about them. Only
the header list and row-building are Leads-specific. PDF is left out for this
MVP (Contacts' PDF export needed a Malayalam font and a fixed column picker
just to stay readable — not worth it yet for a CSV/Excel-first workflow).
"""

import contact_export
from models.lead import Lead

MAX_ROWS = {"csv": 20000, "xlsx": 20000}
MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

HEADERS = [
    "ID", "First name", "Last name", "Phone", "Source", "Branch", "Status", "Score",
    "WhatsApp sent", "Assigned to", "Created by", "Created at", "Address", "Tags", "Notes",
]


def _label(value: str | None) -> str:
    return (value or "").replace("_", " ").title()


def build_rows(leads: list[Lead], employee_names: dict[int, str], source_names: dict[int, str]) -> list[list[str]]:
    """
    employee_names / source_names — {id: name} lookups for assigned_to and
    source_id. Both columns are plain ids with no ORM relationship on Lead
    (unlike Contact), so the caller builds these the same way
    services.lead_service.list_leads() already does, and passes them in.
    `lead.creator` and `lead.branch` ARE real relationships (joinedload them).
    """
    rows = []
    for lead in leads:
        rows.append([
            str(lead.id),
            lead.first_name or "",
            lead.last_name or "",
            lead.phone or "",
            source_names.get(lead.source_id, "") if lead.source_id else "",
            lead.branch.name if lead.branch else "",
            _label(lead.status),
            str(lead.score),
            "Yes" if lead.whatsapp_sent else "No",
            employee_names.get(lead.assigned_to, "") if lead.assigned_to else "",
            lead.creator.name if lead.creator else "",
            lead.created_at.strftime("%Y-%m-%d %H:%M") if lead.created_at else "",
            lead.address or "",
            ", ".join(lead.tags) if lead.tags else "",
            lead.notes or "",
        ])
    return rows


def to_csv(rows: list[list[str]]) -> bytes:
    return contact_export.to_csv(rows, HEADERS)


def to_xlsx(rows: list[list[str]]) -> bytes:
    return contact_export.to_xlsx(rows, HEADERS)
