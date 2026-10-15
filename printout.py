"""Builds the closing report (PDF) for a completed InventorySession.

build_report() gathers plain data; render_pdf() turns it into a PDF. Keeping the
two apart means the same report data can later feed an email body, a CSV export, etc.
"""
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from flask import send_file
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from constants import DENOMINATIONS, business_date
from models import (db, Bar, CashCount, ClosingTask, InventorySession,
                    TaskCompletion)

_styles = getSampleStyleSheet()
TITLE = ParagraphStyle("ReportTitle", parent=_styles["Title"], alignment=0,
                       fontSize=20, spaceAfter=2)
SUBTITLE = ParagraphStyle("ReportSubtitle", parent=_styles["Heading3"], alignment=0,
                          textColor=colors.HexColor("#444444"), spaceAfter=8)
META = ParagraphStyle("Meta", parent=_styles["Normal"], fontSize=10, leading=14,
                      textColor=colors.HexColor("#333333"))
SECTION = ParagraphStyle("Section", parent=_styles["Heading2"], spaceBefore=16,
                         spaceAfter=6, keepWithNext=True)
CATEGORY = ParagraphStyle("Category", parent=_styles["Heading4"], spaceBefore=8,
                          spaceAfter=3, keepWithNext=True)
CELL = ParagraphStyle("Cell", parent=_styles["Normal"], fontSize=9.5, leading=12)
NOTE = ParagraphStyle("Note", parent=_styles["BodyText"], leftIndent=8, spaceAfter=5)


# ------------------------------------------------------------------ the data

def build_report(closing):
    """Collect everything the printout needs as plain Python data.

    The inventory section lists exactly the items counted in THIS closing (including
    ones archived since), and the task section lists the tasks recorded for it, so
    changing the stock or task lists later never rewrites an old report.
    """
    bar = db.session.get(Bar, closing.bar_id)

    previous = (InventorySession.query
                .filter_by(bar_id=closing.bar_id, status="completed")
                .filter(InventorySession.completed_at < closing.completed_at)
                .order_by(InventorySession.completed_at.desc())
                .first())
    previous_counts = {c.item_id: c.quantity for c in previous.counts} if previous else {}

    # Group this closing's counts by category. A count whose item no longer exists
    # (hard-deleted before archiving was introduced) goes into a "Removed items"
    # section instead of crashing the report or silently disappearing.
    grouped = {}
    removed = []
    for count in closing.counts:
        item = count.item
        if item is None or item.category is None:
            removed.append(count)
        else:
            grouped.setdefault(item.category, []).append((item, count.quantity))

    sections = []
    for category, entries in sorted(grouped.items(), key=lambda pair: pair[0].id):
        rows = []
        for item, quantity in sorted(entries, key=lambda pair: pair[0].id):
            before = previous_counts.get(item.id)
            rows.append({
                "name": item.name,
                "previous": before,
                "count": quantity,
                "change": quantity - before if before is not None else None,
            })
        sections.append({"category": category.name, "rows": rows})

    if removed:
        rows = []
        for count in removed:
            before = previous_counts.get(count.item_id)
            rows.append({
                "name": f"Removed item (id {count.item_id})",
                "previous": before,
                "count": count.quantity,
                "change": count.quantity - before if before is not None else None,
            })
        sections.append({"category": "Removed items", "rows": rows})

    cash = CashCount.query.filter_by(session_id=closing.id).first()
    cash_data = None
    if cash:
        labels = {cents: label for _, group in DENOMINATIONS for label, cents in group}
        cash_data = {
            "lines": [
                {"label": labels.get(line.denomination_cents, f"{line.denomination_cents}c"),
                 "quantity": line.quantity,
                 "subtotal_cents": line.denomination_cents * line.quantity}
                for line in sorted(cash.lines, key=lambda l: -l.denomination_cents)
            ],
            "total": cash.total,
            "tip_pool": cash.tip_pool_total,
        }

    task_rows = (db.session.query(TaskCompletion, ClosingTask)
                 .join(ClosingTask, TaskCompletion.task_id == ClosingTask.id)
                 .filter(TaskCompletion.session_id == closing.id)
                 .order_by(ClosingTask.id).all())
    tasks = [{"description": task.description, "done": completion.done}
             for completion, task in task_rows]

    return {
        "bar_name": bar.name,
        "business_date": business_date(closing.completed_at),
        "employee": closing.employee.name,
        "started_at": closing.started_at,
        "completed_at": closing.completed_at,
        "closing_id": closing.id,
        "sections": sections,
        "cash": cash_data,
        "tasks": tasks,
        "notes": [{"time": note.created_at, "text": note.text} for note in closing.notes],
    }


# ------------------------------------------------------------- small helpers

def _safe(text):
    """Escape user text for ReportLab's mini-HTML and keep line breaks."""
    return escape(str(text)).replace("\r\n", "\n").replace("\n", "<br/>")


def _money(amount):
    return f"${amount:,.2f}"


def _show(value):
    return "-" if value is None else str(value)


def _change(value):
    if value is None:
        return "-"
    return "0" if value == 0 else f"{value:+d}"


def _table(data, col_widths, right_cols=(), extra_style=()):
    table = Table(data, colWidths=col_widths, repeatRows=1)  # header repeats per page
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#bbbbbb")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    style += [("ALIGN", (col, 0), (col, -1), "RIGHT") for col in right_cols]
    style += list(extra_style)
    table.setStyle(TableStyle(style))
    return table


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#888888"))
    canvas.drawString(doc.leftMargin, 0.5 * inch,
                      f"{doc.title} - generated {datetime.now():%Y-%m-%d %H:%M}")
    canvas.drawRightString(letter[0] - doc.rightMargin, 0.5 * inch, f"Page {doc.page}")
    canvas.restoreState()


# ------------------------------------------------------------------- the PDF

def render_pdf(report):
    """Turn a report dict into PDF bytes (returned as a BytesIO)."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        title=f"{report['bar_name']} closing report {report['business_date']:%Y-%m-%d}",
        author=report["bar_name"],
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.85 * inch,
    )
    story = []

    # Header
    story.append(Paragraph(_safe(report["bar_name"]), TITLE))
    story.append(Paragraph(
        f"Closing report - {report['business_date']:%A, %B %d, %Y}", SUBTITLE))
    for line in (
        f"<b>Closed by:</b> {_safe(report['employee'])}",
        f"<b>Started:</b> {report['started_at']:%Y-%m-%d %H:%M}",
        f"<b>Submitted:</b> {report['completed_at']:%Y-%m-%d %H:%M}",
        f"<b>Closing #:</b> {report['closing_id']}",
    ):
        story.append(Paragraph(line, META))

    # Summary line
    all_rows = [row for section in report["sections"] for row in section["rows"]]
    counted = sum(1 for row in all_rows if row["count"] is not None)
    done = sum(1 for task in report["tasks"] if task["done"])
    cash_text = _money(report["cash"]["total"]) if report["cash"] else "not recorded"
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        f"<b>Summary:</b> {counted} of {len(all_rows)} items counted - cash subtotal "
        f"{cash_text} - {done} of {len(report['tasks'])} tasks done - "
        f"{len(report['notes'])} note(s)", META))

    # Inventory
    story.append(Paragraph("Inventory", SECTION))
    widths = [3.8 * inch, 1.0 * inch, 1.0 * inch, 1.2 * inch]
    for section in report["sections"]:
        story.append(Paragraph(_safe(section["category"]), CATEGORY))
        if not section["rows"]:
            story.append(Paragraph("No items in this category.", META))
            continue
        data = [["Item", "Previous", "Count", "Change"]]
        for row in section["rows"]:
            data.append([Paragraph(_safe(row["name"]), CELL), _show(row["previous"]),
                         _show(row["count"]), _change(row["change"])])
        story.append(_table(data, widths, right_cols=(1, 2, 3)))

    # Cash
    story.append(Paragraph("Cash", SECTION))
    cash = report["cash"]
    if cash is None:
        story.append(Paragraph("No cash count was recorded.", META))
    else:
        data = [["Denomination", "Quantity", "Subtotal"]]
        for line in cash["lines"]:
            data.append([line["label"], str(line["quantity"]),
                         _money(line["subtotal_cents"] / 100)])
        data.append(["Cash subtotal", "", _money(cash["total"])])
        total_row = len(data) - 1
        if cash["tip_pool"] is not None:
            data.append(["Tip pool", "", _money(cash["tip_pool"])])
        story.append(_table(
            data, [3.0 * inch, 1.5 * inch, 1.5 * inch], right_cols=(1, 2),
            extra_style=[("FONTNAME", (0, total_row), (-1, total_row), "Helvetica-Bold")]))

    # Closing tasks
    story.append(Paragraph("Closing tasks", SECTION))
    if not report["tasks"]:
        story.append(Paragraph("No tasks were defined.", META))
    else:
        data = [["Task", "Status"]]
        for task in report["tasks"]:
            data.append([Paragraph(_safe(task["description"]), CELL),
                         "Done" if task["done"] else "Not done"])
        story.append(_table(data, [5.5 * inch, 1.5 * inch]))

    # Notes
    story.append(Paragraph("Notes", SECTION))
    if not report["notes"]:
        story.append(Paragraph("No notes.", META))
    for note in report["notes"]:
        story.append(Paragraph(f"<b>{note['time']:%H:%M}</b> - {_safe(note['text'])}", NOTE))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    buffer.seek(0)
    return buffer


def pdf_filename(closing):
    return f"closing_{business_date(closing.completed_at).isoformat()}_{closing.id}.pdf"


def pdf_response(closing, as_attachment=False):
    """Flask response that shows the PDF in the browser, or downloads it."""
    return send_file(render_pdf(build_report(closing)), mimetype="application/pdf",
                     as_attachment=as_attachment, download_name=pdf_filename(closing))