"""The fee PDFs: a student's statement and a paid fee's receipt.

Rendered on this machine with ReportLab, never by a remote service, because
both carry the student's name, USN and payments (rule 1). Built from
`fees.FeeStatement`, the same object the My fees screen reads, so the PDF and
the screen cannot disagree about a number.

Amounts print as "Rs 12,500": the standard PDF fonts have no rupee glyph, and
a missing glyph prints as a black box on the college's own receipt.
"""
from __future__ import annotations

import io
from typing import Final

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .fees import FeeStatement, StatementLine
from .models.fee import DISPLAY_OVERDUE, FEE_HEAD_LABELS, QUOTA_LABELS

PAYMENT_METHOD_LABELS: Final[dict[str, str]] = {
    "RAZORPAY": "Online (Razorpay)",
    "CASH": "Cash",
    "CHEQUE": "Cheque",
    "DEMAND_DRAFT": "Demand draft",
    "BANK_TRANSFER": "Bank transfer",
}

STATUS_LABELS: Final[dict[str, str]] = {
    "PENDING": "Pending",
    "PAID": "Paid",
    DISPLAY_OVERDUE: "Overdue",
    "CANCELLED": "Cancelled",
}

_BRAND = colors.HexColor("#5b2a86")
_HAIRLINE = colors.HexColor("#d9cfe8")


def rupees(paise: int) -> str:
    rupee, rest = divmod(paise, 100)
    text = f"Rs {rupee:,}"
    return f"{text}.{rest:02d}" if rest else text


def _quota(value: str | None) -> str:
    return QUOTA_LABELS.get(value or "", "Not recorded")


def _styles():
    sheet = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=sheet["Title"], textColor=_BRAND, alignment=0, fontSize=18),
        "body": sheet["BodyText"],
        "small": ParagraphStyle("s", parent=sheet["BodyText"], fontSize=8, textColor=colors.grey),
    }


def _key_value_table(rows: list[tuple[str, str]]) -> Table:
    table = Table(rows, colWidths=[45 * mm, 120 * mm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TEXTCOLOR", (0, 0), (0, -1), _BRAND),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def _student_rows(statement: FeeStatement) -> list[tuple[str, str]]:
    s = statement.student
    return [
        ("Student", s.name),
        ("Student ID (USN)", s.usn or "-"),
        ("Course", s.course_name or "-"),
        ("Department", s.department_name or "-"),
        ("Batch", s.batch_label or "-"),
        ("Quota", _quota(s.quota)),
    ]


def _build(story: list) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm, title="REEP fees",
    )
    doc.build(story)
    return buffer.getvalue()


def render_fee_statement(statement: FeeStatement) -> bytes:
    st = _styles()
    totals = statement.totals
    story: list = [
        Paragraph("Fee statement", st["title"]),
        Paragraph(
            f"Academic year {statement.academic_year} &middot; generated on "
            f"{statement.generated_on:%d %b %Y}",
            st["small"],
        ),
        Spacer(1, 6 * mm),
        _key_value_table(_student_rows(statement)),
        Spacer(1, 6 * mm),
        _key_value_table([
            ("Total raised", rupees(totals.total_raised_paise)),
            ("Paid", rupees(totals.paid_paise)),
            ("Balance to pay", rupees(totals.balance_paise)),
        ]),
        Spacer(1, 6 * mm),
    ]
    data = [["Fee", "Year", "Amount", "Due date", "Status", "Receipt"]]
    for line in statement.lines:
        data.append([
            line.title,
            line.academic_year or "-",
            rupees(line.amount_paise),
            f"{line.due_on:%d %b %Y}" if line.due_on else "-",
            STATUS_LABELS.get(line.display_status, line.display_status),
            line.receipt_number or "-",
        ])
    if len(data) == 1:
        data.append(["No fees raised yet.", "", "", "", "", ""])
    table = Table(data, repeatRows=1, colWidths=[42 * mm, 20 * mm, 26 * mm, 26 * mm, 20 * mm, 40 * mm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), _BRAND),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, _HAIRLINE),
        ("ALIGN", (2, 1), (2, -1), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    return _build(story)


def render_fee_receipt(statement: FeeStatement, line: StatementLine) -> bytes:
    st = _styles()
    paid_at = f"{line.paid_at:%d %b %Y, %H:%M} UTC" if line.paid_at else "-"
    payment_rows = [
        ("Receipt number", line.receipt_number or "-"),
        ("Fee", line.title),
        ("Fee head", FEE_HEAD_LABELS.get(line.fee_head or "", line.fee_head or "-")),
        ("Academic year", line.academic_year or "-"),
        ("Amount paid", rupees(line.amount_paise)),
        ("Paid on", paid_at),
        ("Paid by", PAYMENT_METHOD_LABELS.get(line.payment_method or "", line.payment_method or "-")),
    ]
    if line.payment_reference:
        payment_rows.append(("Reference", line.payment_reference))
    if line.razorpay_payment_id:
        payment_rows.append(("Razorpay payment ID", line.razorpay_payment_id))
    story = [
        Paragraph("Fee receipt", st["title"]),
        Spacer(1, 4 * mm),
        _key_value_table(_student_rows(statement)),
        Spacer(1, 6 * mm),
        _key_value_table(payment_rows),
        Spacer(1, 10 * mm),
        Paragraph("This receipt was generated by REEP and needs no signature.", st["small"]),
    ]
    return _build(story)
