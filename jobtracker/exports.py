"""Download formats for the tracker: CSV, Excel and PDF."""
from __future__ import annotations

import csv
import io
from datetime import date
from xml.sax.saxutils import escape

from . import VALID_STATUSES

COLUMNS = ["link", "position", "date of publication", "status"]


def _row(job) -> dict:
    return {"link": job["link"], "position": job["position"], "date": job["date_published"] or "",
            "status": job["status"], "company": job["company"] or "", "source": job["source"] or ""}


def to_csv(jobs) -> bytes:
    """Same four columns as the tracker CSV, so the file can be re-imported."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(COLUMNS)
    for job in map(_row, jobs):
        writer.writerow([job["link"], job["position"], job["date"], job["status"]])
    return buffer.getvalue().encode("utf-8-sig")


def to_xlsx(jobs) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    book = Workbook()
    sheet = book.active
    sheet.title = "Jobs"
    sheet.append([*COLUMNS, "company", "source"])
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F3A5F")
    for job in map(_row, jobs):
        sheet.append([job["link"], job["position"], job["date"], job["status"], job["company"], job["source"]])
        link = sheet.cell(row=sheet.max_row, column=1)
        link.hyperlink = job["link"]
        link.font = Font(color="1155CC", underline="single")
    for column, width in zip("ABCDEF", (60, 50, 20, 20, 28, 18), strict=True):
        sheet.column_dimensions[column].width = width
    for row in sheet.iter_rows(min_row=2, max_col=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=False)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    validation = DataValidation(type="list", formula1='"' + ",".join(VALID_STATUSES) + '"', allow_blank=False)
    sheet.add_data_validation(validation)
    validation.add(f"D2:D{max(2, sheet.max_row + 500)}")
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _pdf_text(value: str) -> str:
    # The built-in PDF fonts cover Latin-1 (German umlauts included). Replace anything else.
    return escape(str(value).encode("latin-1", "replace").decode("latin-1"))


def to_pdf(jobs, title: str = "Job tracker") -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    cell = styles["BodyText"]
    cell.fontSize, cell.leading = 8, 10
    rows = [["Position", "Company", "Published", "Status"]]
    jobs = list(map(_row, jobs))
    for job in jobs:
        position = f'<link href="{_pdf_text(job["link"])}" color="#1155CC">{_pdf_text(job["position"])}</link>'
        rows.append([Paragraph(position, cell), Paragraph(_pdf_text(job["company"]), cell),
                     job["date"], job["status"]])
    table = Table(rows, colWidths=[140 * mm, 60 * mm, 28 * mm, 38 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3A5F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F5F8")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D1DC")),
    ]))
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), title=title,
                            leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm)
    doc.build([Paragraph(_pdf_text(title), styles["Title"]),
               Paragraph(f"{len(jobs)} postings, exported {date.today().isoformat()}. "
                         "Position titles are links.", styles["Normal"]),
               Spacer(1, 6 * mm), table])
    return buffer.getvalue()
