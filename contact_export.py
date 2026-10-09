"""Contact export to CSV, Excel and PDF from the same row set."""

import csv
import datetime
import io
import os

from models import Contact

MAX_ROWS = {"csv": 20000, "xlsx": 20000, "pdf": 2000}
MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}

# Column order matches the import template, so an export can be edited and imported back.
HEADERS = [
    "ID", "Type", "Name", "Phone", "Alternate phone", "District", "Address", "Lead source",
    "Stage", "Assigned to", "Created by", "Created at", "Verified",
    "Profession", "Work area", "House stage", "Current tank", "Notes",
]
PDF_COLUMNS = ["Name", "Phone", "Type", "District", "Lead source", "Stage", "Assigned to", "Created at"]


def _label(value: str | None) -> str:
    return (value or "").replace("_", " ").title()


def build_rows(items: list[tuple[Contact, object, bool]]) -> list[list[str]]:
    """items: (contact, current lead stage or None, is_verified)."""
    rows = []
    for c, stage, verified in items:
        consumer, partner = c.consumer_profile, c.partner_profile
        tank = ""
        if consumer and (consumer.current_tank_type or consumer.current_tank_capacity):
            tank = " ".join(str(x) for x in (consumer.current_tank_type, f"{consumer.current_tank_capacity} L" if consumer.current_tank_capacity else None) if x)
        rows.append([
            str(c.id), _label(c.contact_type), c.name, c.phone, c.phone_alt or "", c.district, c.address or "",
            c.lead_source.name if c.lead_source else "", _label(stage.stage) if stage else "",
            c.assigned_employee.name if c.assigned_employee else "", c.creator.name if c.creator else "",
            c.created_at.strftime("%Y-%m-%d %H:%M") if c.created_at else "", "Yes" if verified else "No",
            _label(partner.profession_type) if partner else "", partner.work_area or "" if partner else "",
            consumer.house_stage or "" if consumer else "", tank, c.notes or "",
        ])
    return rows


def _safe(cell: str) -> str:
    """Stop spreadsheet apps running a cell that starts like a formula."""
    return "'" + cell if cell[:1] in ("=", "+", "-", "@") and not cell[1:].replace(" ", "").isdigit() else cell


def to_csv(rows: list[list[str]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_ALL)
    writer.writerow(HEADERS)
    writer.writerows([[_safe(x) for x in row] for row in rows])
    return ("\ufeff" + out.getvalue()).encode("utf-8")  # BOM keeps Excel from mangling Malayalam names


def to_xlsx(rows: list[list[str]]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Contacts"
    ws.append(HEADERS)
    for row in rows:
        ws.append([_safe(x) for x in row])  # strings only, so phones stay "+919876543210"
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="0A0A4A")
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for i, header in enumerate(HEADERS, start=1):
        longest = max([len(header)] + [len(r[i - 1]) for r in rows[:500]])
        ws.column_dimensions[get_column_letter(i)].width = min(max(longest + 2, 8), 45)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_FONT_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "assets", "fonts", "NotoSansMalayalam-Regular.ttf"),
    "C:/Windows/Fonts/Nirmala.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansMalayalam-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _pdf_font() -> str:
    """A font with Malayalam glyphs when the server has one; Helvetica otherwise."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("ExportFont", path))
                return "ExportFont"
            except Exception:
                continue
    return "Helvetica"


def to_pdf(rows: list[list[str]], filter_summary: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    font = _pdf_font()
    small = ParagraphStyle("cell", fontName=font, fontSize=7.5, leading=9)
    head = ParagraphStyle("head", parent=small, textColor=colors.white)
    title = ParagraphStyle("title", fontName=font, fontSize=14, leading=18)
    meta = ParagraphStyle("meta", fontName=font, fontSize=8, leading=11, textColor=colors.HexColor("#64748b"))

    picks = [HEADERS.index(name) for name in PDF_COLUMNS]
    data = [[Paragraph(escape(name), head) for name in PDF_COLUMNS]]
    for row in rows:
        data.append([Paragraph(escape(row[i]), small) for i in picks])

    widths = [150, 85, 55, 80, 80, 70, 100, 80]
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0a0a4a")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef2ff")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))

    def footer(canvas, doc):
        canvas.setFont("Helvetica", 7)
        canvas.drawRightString(landscape(A4)[0] - 28, 16, f"Page {doc.page}")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=28, rightMargin=28, topMargin=28, bottomMargin=28, title="Contacts export")
    stamp = datetime.date.today().strftime("%d %b %Y")
    doc.build([
        Paragraph("Contacts export", title),
        Paragraph(escape(f"Generated {stamp} · {len(rows)} contacts" + (f" · {filter_summary}" if filter_summary else "")), meta),
        Spacer(1, 8),
        table,
    ], onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
