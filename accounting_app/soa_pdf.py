"""A printable Statement of Account PDF.

Laid out the way a statement actually gets sent to a customer or supplier:
the company's own letterhead at the top left, the party it is addressed to on
the right, then the invoice listing, the ageing summary, and anything received
but not yet matched.
"""
import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle, Paragraph,
                                Spacer, KeepTogether)

BRAND = colors.HexColor("#2563AB")
LINE = colors.HexColor("#CBD5E1")
SOFT = colors.HexColor("#F1F5F9")
MUTED = colors.HexColor("#64748B")
DUE = colors.HexColor("#B91C1C")
CREDIT = colors.HexColor("#166534")
CREDIT_BG = colors.HexColor("#F3FBF5")


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontSize=17,
                                textColor=BRAND, spaceAfter=2, alignment=0),
        "company": ParagraphStyle("c", parent=base["Normal"], fontSize=12,
                                  leading=15, spaceAfter=1),
        "small": ParagraphStyle("s", parent=base["Normal"], fontSize=8.2,
                                leading=11, textColor=MUTED),
        "label": ParagraphStyle("l", parent=base["Normal"], fontSize=7.5,
                                leading=10, textColor=MUTED),
        "body": ParagraphStyle("b", parent=base["Normal"], fontSize=9,
                               leading=12),
        "cell": ParagraphStyle("ce", parent=base["Normal"], fontSize=8,
                               leading=10),
        "right": ParagraphStyle("r", parent=base["Normal"], fontSize=9,
                                leading=12, alignment=TA_RIGHT),
        "section": ParagraphStyle("se", parent=base["Normal"], fontSize=10,
                                  leading=13, textColor=BRAND,
                                  spaceBefore=10, spaceAfter=4),
    }


def _money(value):
    try:
        return f"{float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _company_block(company, st):
    """Letterhead: whatever the company has filled in, nothing empty."""
    name = (company or {}).get("company_name") or "Statement of Account"
    lines = []
    for key in ("address_line1", "address_line2"):
        if (company or {}).get(key):
            lines.append(company[key])
    locality = ", ".join(
        str((company or {}).get(k)) for k in ("city", "state", "country")
        if (company or {}).get(k))
    if locality:
        lines.append(locality)
    contact = " | ".join(
        str((company or {}).get(k)) for k in ("phone", "email")
        if (company or {}).get(k))
    if contact:
        lines.append(contact)
    if (company or {}).get("vat_registration_number"):
        lines.append(f"TRN: {company['vat_registration_number']}")

    flow = [Paragraph(f"<b>{name}</b>", st["company"])]
    if lines:
        flow.append(Paragraph("<br/>".join(lines), st["small"]))
    return flow


def _party_block(statement, st):
    """Who the statement is addressed to."""
    lines = []
    if statement.get("contact_person"):
        lines.append(f"Attn: {statement['contact_person']}")
    if statement.get("address"):
        lines.extend(str(statement["address"]).splitlines())
    contact = " | ".join(str(statement.get(k)) for k in ("phone", "email")
                         if statement.get(k))
    if contact:
        lines.append(contact)
    if statement.get("trn"):
        lines.append(f"TRN: {statement['trn']}")

    flow = [Paragraph("STATEMENT FOR", st["label"]),
            Paragraph(f"<b>{statement['ledger_name']}</b>", st["body"])]
    if lines:
        flow.append(Paragraph("<br/>".join(lines), st["small"]))
    return flow


def build_statement_pdf(statement, company=None):
    """The statement as a PDF, returned as a BytesIO ready to send."""
    st = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, title=f"Statement of Account - {statement['ledger_name']}",
        author=(company or {}).get("company_name") or "",
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=14 * mm, bottomMargin=16 * mm)
    width = doc.width
    flow = []

    # ---- letterhead + heading -------------------------------------------
    heading = [
        Paragraph("STATEMENT OF ACCOUNT", st["title"]),
        Paragraph(f"As at <b>{statement['as_of_date']}</b>", st["small"]),
    ]
    header = Table([[_company_block(company, st), heading]],
                   colWidths=[width * 0.55, width * 0.45])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    flow += [header, Spacer(1, 8)]

    rule = Table([[""]], colWidths=[width], rowHeights=[2])
    rule.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), BRAND)]))
    flow += [rule, Spacer(1, 10)]

    # ---- addressee + summary --------------------------------------------
    totals = statement["totals"]
    owed_label = ("Amount receivable" if statement["party_kind"] == "Customer"
                  else "Amount payable")
    summary = Table([
        [Paragraph("Outstanding items", st["label"]),
         Paragraph(str(len(statement["rows"])), st["right"])],
        [Paragraph("Original invoiced", st["label"]),
         Paragraph(_money(totals["original"]), st["right"])],
        [Paragraph("Less matched / settled", st["label"]),
         Paragraph(_money(totals["matched"]), st["right"])],
        [Paragraph("Less unmatched credits", st["label"]),
         Paragraph(_money(totals["unallocated"]), st["right"])],
        [Paragraph(f"<b>{owed_label}</b>", st["body"]),
         Paragraph(f"<b>{_money(totals['net_outstanding'])}</b>", st["right"])],
    ], colWidths=[width * 0.26, width * 0.17])
    summary.setStyle(TableStyle([
        ("LINEABOVE", (0, 4), (-1, 4), 0.8, LINE),
        ("BACKGROUND", (0, 4), (-1, 4), SOFT),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    top = Table([[_party_block(statement, st), summary]],
                colWidths=[width * 0.55, width * 0.45])
    top.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    flow += [top, Spacer(1, 12)]

    # ---- the invoices ----------------------------------------------------
    flow.append(Paragraph("Outstanding items", st["section"]))
    # Dates and figures are written as plain strings, not Paragraphs: a
    # Paragraph wraps, and a wrapped date reads as "2023-07-2 / 7". Only the
    # reference can be long enough to need wrapping.
    # The voucher type is shown because not every open item is an invoice: a
    # Receipt credited to a supplier, for instance, also increases the balance,
    # and without the type column it would read as a purchase invoice.
    head = ["Date", "Voucher", "Type", "Due Date", "Days Late",
            "Original", "Matched", "Remaining", "Balance"]
    data = [head]

    for row in statement["rows"]:
        overdue = row["days_overdue"]
        data.append([
            row["date"],
            row["voucher_number"],
            row["voucher_type"],
            row["due_date"] or "",
            str(overdue) if overdue else "",
            _money(row["original_amount"]),
            _money(row["matched_amount"]) if row["matched_amount"] else "",
            _money(row["remaining_amount"]),
            _money(row["balance"]),
        ])

    if not statement["rows"]:
        data.append(["Nothing outstanding - every item is settled."] + [""] * 8)

    widths = [w * width for w in (0.105, 0.165, 0.125, 0.105, 0.07,
                                  0.11, 0.10, 0.11, 0.11)]
    table = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BRAND),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("GRID", (0, 0), (-1, -1), 0.25, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (4, 0), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFBFC")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for index, row in enumerate(statement["rows"], start=1):
        if row["days_overdue"] > 0:
            style.append(("TEXTCOLOR", (4, index), (4, index), DUE))
        if not row.get("is_invoice", True):
            # A credit reduces the balance - shade the row so it is obvious
            # that it is not another invoice.
            style += [("BACKGROUND", (0, index), (-1, index), CREDIT_BG),
                      ("TEXTCOLOR", (7, index), (7, index), CREDIT)]
    if statement["rows"]:
        closing = ("Closing balance receivable"
                   if statement["party_kind"] == "Customer"
                   else "Closing balance payable")
        data.append([
            f"{len(statement['rows'])} open item(s) - {closing}", "", "", "", "",
            _money(totals["original"]), _money(totals["matched"]), "",
            _money(totals["net_outstanding"]),
        ])
        last = len(data) - 1
        style += [("BACKGROUND", (0, last), (-1, last), SOFT),
                  ("SPAN", (0, last), (4, last)),
                  ("FONTNAME", (0, last), (-1, last), "Helvetica-Bold"),
                  ("LINEABOVE", (0, last), (-1, last), 0.8, BRAND)]
    table.setStyle(TableStyle(style))
    flow.append(table)

    # ---- ageing ----------------------------------------------------------
    if statement["rows"]:
        ageing = statement["ageing"]
        buckets = list(ageing.keys())
        ageing_table = Table(
            [[Paragraph(f"<b>{b}</b>", st["cell"]) for b in buckets] +
             [Paragraph("<b>Total</b>", st["cell"])],
             [Paragraph(_money(ageing[b]), st["cell"]) for b in buckets] +
             [Paragraph(f"<b>{_money(totals['remaining'])}</b>", st["cell"])]],
            colWidths=[width / (len(buckets) + 1)] * (len(buckets) + 1))
        ageing_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), SOFT),
            ("GRID", (0, 0), (-1, -1), 0.25, LINE),
            ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        flow.append(KeepTogether([
            Paragraph("Ageing of the remaining balance", st["section"]),
            ageing_table]))

    # Credits now sit inline in the listing above, shaded, so there is no
    # separate table to reconcile against.
    if statement["unallocated"]:
        flow += [Spacer(1, 6), Paragraph(
            "Shaded lines are credits - payments, refunds or notes that reduce "
            "the balance but have not been matched to a specific invoice. "
            "Matching them will clear the invoices they belong to.", st["small"])]

    if not statement["reconciles"]:
        flow += [Spacer(1, 8), Paragraph(
            f"Note: this statement comes to {_money(totals['net_outstanding'])} "
            f"while the ledger balance is {_money(statement['ledger_balance'])} "
            f"(difference {_money(statement['difference'])}). An opening balance "
            f"entered directly on the ledger is the usual cause.", st["small"])]

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(15 * mm, 10 * mm,
                          f"{(company or {}).get('company_name') or ''}"
                          f"   |   Statement as at {statement['as_of_date']}")
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Page {document.page}")
        canvas.restoreState()

    doc.build(flow, onFirstPage=footer, onLaterPages=footer)
    buffer.seek(0)
    return buffer
