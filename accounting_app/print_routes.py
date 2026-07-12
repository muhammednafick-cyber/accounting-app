from flask import Blueprint, render_template, request, flash, redirect, url_for, send_file, session
from flask_login import login_required, current_user
import io
import xlsxwriter
from database import get_connection, get_current_company_id
from .models import format_date

print_bp = Blueprint("print_bp", __name__)


def _fetch_voucher_for_print(voucher_number):
    """Load voucher header + ledger/item entries for the JV view and its exports."""
    company_id = get_current_company_id()
    conn = get_connection()
    try:
        cursor = conn.cursor()

        def _val(row, key, index):
            return row[key] if isinstance(row, dict) or hasattr(row, 'keys') else row[index]

        cursor.execute(
            """
            SELECT voucher_number, voucher_type, date, cost_center_code, narration, location_name
            FROM vouchers
            WHERE LOWER(voucher_number) = LOWER(%s) AND company_id = %s
            """,
            (voucher_number, company_id),
        )
        voucher = cursor.fetchone()
        if not voucher:
            return None, [], []

        db_voucher_number = _val(voucher, 'voucher_number', 0)
        voucher_data = {
            "voucher_number": _val(voucher, 'voucher_number', 0),
            "voucher_type": _val(voucher, 'voucher_type', 1),
            "date": format_date(_val(voucher, 'date', 2)),
            "cost_center_code": _val(voucher, 'cost_center_code', 3),
            "narration": _val(voucher, 'narration', 4),
            "location_name": _val(voucher, 'location_name', 5),
        }

        if voucher_data["cost_center_code"]:
            cursor.execute(
                "SELECT center_name FROM cost_centers WHERE center_code = %s AND company_id = %s",
                (voucher_data["cost_center_code"], company_id),
            )
            result = cursor.fetchone()
            voucher_data["cost_center_name"] = _val(result, 'center_name', 0) if result else None

        cursor.execute(
            """
            SELECT ledger_name, amount, type
            FROM ledger_entries
            WHERE voucher_number = %s AND company_id = %s
            ORDER BY type DESC
            """,
            (db_voucher_number, company_id),
        )
        ledger_entries = [
            {"ledger_name": _val(row, 'ledger_name', 0), "amount": _val(row, 'amount', 1), "type": _val(row, 'type', 2)}
            for row in cursor.fetchall()
        ]

        cursor.execute(
            """
            SELECT item_name, quantity, unit_price, amount, ledger_name, type, location_name
            FROM item_entries
            WHERE voucher_number = %s AND company_id = %s
            """,
            (db_voucher_number, company_id),
        )
        item_entries = [
            {
                "item_name": _val(row, 'item_name', 0),
                "quantity": _val(row, 'quantity', 1),
                "unit_price": _val(row, 'unit_price', 2),
                "amount": _val(row, 'amount', 3),
                "ledger_name": _val(row, 'ledger_name', 4),
                "type": _val(row, 'type', 5),
                "location_name": _val(row, 'location_name', 6),
            }
            for row in cursor.fetchall()
        ]
        return voucher_data, ledger_entries, item_entries
    finally:
        conn.close()


@print_bp.route("/print/jv/export")
@login_required
def export_jv_excel():
    """Export a single journal entry (voucher) to Excel — same content as the JV print view."""
    voucher_number = (request.args.get("voucher_number") or "").strip()
    if not voucher_number:
        flash("Voucher number is required", "error")
        return redirect(url_for("print_bp.print_jv"))

    voucher_data, ledger_entries, item_entries = _fetch_voucher_for_print(voucher_number)
    if not voucher_data:
        flash(f"Voucher {voucher_number} not found", "error")
        return redirect(url_for("print_bp.print_jv"))

    excluded_types = ["Sales", "Purchase", "Sales Return", "Purchase Return", "Stock Adjustment"]
    if voucher_data["voucher_type"] in excluded_types:
        item_entries = []

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    ws = workbook.add_worksheet("Voucher")

    title_fmt = workbook.add_format({'bold': True, 'font_size': 14})
    label_fmt = workbook.add_format({'bold': True})
    header_fmt = workbook.add_format({'bold': True, 'bg_color': '#2563AB', 'font_color': '#FFFFFF', 'border': 1})
    cell_fmt = workbook.add_format({'border': 1})
    num_fmt = workbook.add_format({'border': 1, 'num_format': '#,##0.00'})
    total_fmt = workbook.add_format({'bold': True, 'border': 1, 'num_format': '#,##0.00', 'bg_color': '#F3F6FB'})

    ws.set_column(0, 0, 40)
    ws.set_column(1, 3, 14)

    company_name = session.get('company_name', '')
    row = 0
    if company_name:
        ws.write(row, 0, company_name, title_fmt)
        row += 1
    ws.write(row, 0, f"{voucher_data['voucher_type']} Voucher", title_fmt)
    row += 2
    ws.write(row, 0, "Voucher No:", label_fmt); ws.write(row, 1, voucher_data['voucher_number']); row += 1
    ws.write(row, 0, "Date:", label_fmt); ws.write(row, 1, voucher_data['date']); row += 1
    if voucher_data.get('location_name'):
        ws.write(row, 0, "Location:", label_fmt); ws.write(row, 1, voucher_data['location_name']); row += 1
    if voucher_data.get('cost_center_name'):
        ws.write(row, 0, "Cost Center:", label_fmt); ws.write(row, 1, voucher_data['cost_center_name']); row += 1
    row += 1

    for col, h in enumerate(["Particulars", "Type", "Debit", "Credit"]):
        ws.write(row, col, h, header_fmt)
    row += 1

    total_debit = total_credit = 0.0
    for item in item_entries:
        particulars = f"{item['item_name']} (Qty: {item['quantity']} @ {item['unit_price']})"
        amount = float(item['amount'] or 0)
        ws.write(row, 0, particulars, cell_fmt)
        ws.write(row, 1, item['type'], cell_fmt)
        ws.write_number(row, 2, amount if item['type'] == 'Debit' else 0, num_fmt)
        ws.write_number(row, 3, amount if item['type'] == 'Credit' else 0, num_fmt)
        if item['type'] == 'Debit':
            total_debit += amount
        else:
            total_credit += amount
        row += 1
    for entry in ledger_entries:
        amount = float(entry['amount'] or 0)
        ws.write(row, 0, entry['ledger_name'], cell_fmt)
        ws.write(row, 1, entry['type'], cell_fmt)
        ws.write_number(row, 2, amount if entry['type'] == 'Debit' else 0, num_fmt)
        ws.write_number(row, 3, amount if entry['type'] == 'Credit' else 0, num_fmt)
        if entry['type'] == 'Debit':
            total_debit += amount
        else:
            total_credit += amount
        row += 1

    ws.write(row, 0, "Total", total_fmt)
    ws.write(row, 1, "", total_fmt)
    ws.write_number(row, 2, total_debit, total_fmt)
    ws.write_number(row, 3, total_credit, total_fmt)
    row += 2
    ws.write(row, 0, "Narration:", label_fmt)
    ws.write(row, 1, voucher_data.get('narration') or "")

    workbook.close()
    output.seek(0)
    return send_file(
        output,
        download_name=f"{voucher_data['voucher_number']}.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

@print_bp.route("/print/documents")
@login_required
def print_documents():
    """Pick a voucher type, then a document number, then print in the
    applicable format (Tax Invoice / Tax Credit Note / Item Details / JV)."""
    company_id = get_current_company_id()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT voucher_type FROM vouchers WHERE company_id = %s ORDER BY voucher_type", (company_id,))
        voucher_types = [r[0] for r in cursor.fetchall()]
    finally:
        conn.close()
    return render_template(
        "print_documents.html",
        voucher_types=voucher_types,
        username=current_user.username,
    )


@print_bp.route("/api/print/voucher_numbers")
@login_required
def api_print_voucher_numbers():
    """Document numbers available for a voucher type (newest first)."""
    from flask import jsonify
    company_id = get_current_company_id()
    voucher_type = (request.args.get("voucher_type") or "").strip()
    if not voucher_type:
        return jsonify({"success": False, "message": "voucher_type is required"}), 400
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT voucher_number, date, amount, COALESCE(narration, '')
            FROM vouchers WHERE company_id = %s AND voucher_type = %s
            ORDER BY date DESC, voucher_id DESC LIMIT 500
        """, (company_id, voucher_type))
        docs = [
            {"voucher_number": r[0], "date": format_date(r[1]), "amount": float(r[2] or 0), "narration": r[3]}
            for r in cursor.fetchall()
        ]
        return jsonify({"success": True, "documents": docs})
    finally:
        conn.close()


def _fetch_voucher_extras(voucher_number):
    """Extra header fields + company profile for invoice/credit-note prints."""
    company_id = get_current_company_id()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT due_date, original_invoice_ref, original_invoice_date, credit_days, linked_voucher_number
            FROM vouchers WHERE LOWER(voucher_number) = LOWER(%s) AND company_id = %s
        """, (voucher_number, company_id))
        row = cursor.fetchone()
        extras = {}
        if row:
            extras = {
                'due_date': row[0], 'original_invoice_ref': row[1],
                'original_invoice_date': row[2], 'credit_days': row[3],
                'linked_voucher_number': row[4],
            }
        cursor.execute("""
            SELECT company_name, vat_registration_number, address_line1, address_line2,
                   city, country, phone, email, vat_applicable, currency_code
            FROM company_settings WHERE company_id = %s
        """, (company_id,))
        c = cursor.fetchone()
        company = {}
        if c:
            company = {
                'name': c[0], 'trn': c[1], 'address1': c[2], 'address2': c[3],
                'city': c[4], 'country': c[5], 'phone': c[6], 'email': c[7],
                'vat_applicable': c[8], 'currency': c[9] or 'AED',
            }
        return extras, company
    finally:
        conn.close()


SYSTEM_PRINT_LEDGERS = {'Output VAT 5%', 'Input VAT 5%', 'Cost of Goods Sold', 'Inventory',
                        'Discount Allowed', 'Discount Received'}


@print_bp.route("/print/voucher/<voucher_number>")
@login_required
def print_voucher(voucher_number):
    """Dispatch to the right print format per voucher type:
    Sales / Service Income -> Tax Invoice; Sales Return / Service Income Return
    -> Tax Credit Note (UAE VAT format when VAT is applicable);
    Purchase / Purchase Return -> voucher with item details; others -> JV print."""
    voucher_number = voucher_number.strip()
    voucher_data, ledger_entries, item_entries = _fetch_voucher_for_print(voucher_number)
    if not voucher_data:
        flash(f"Voucher {voucher_number} not found", "error")
        return redirect(url_for("print_bp.print_jv"))

    vtype = voucher_data["voucher_type"]
    extras, company = _fetch_voucher_extras(voucher_number)
    voucher_data.update(extras)

    if vtype in ("Sales", "Sales Return", "Service Income", "Service Income Return"):
        vat_total = sum(le['amount'] or 0 for le in ledger_entries if le['ledger_name'] in ('Output VAT 5%', 'Input VAT 5%'))
        discount_total = sum(le['amount'] or 0 for le in ledger_entries if le['ledger_name'] in ('Discount Allowed', 'Discount Received'))
        # Party = the non-system ledger on the gross side
        party = next((le['ledger_name'] for le in ledger_entries
                      if le['ledger_name'] not in SYSTEM_PRINT_LEDGERS
                      and le['type'] == ('Debit' if vtype in ('Sales', 'Service Income') else 'Credit')), '')
        if vtype in ("Service Income", "Service Income Return"):
            # Service lines = income-side ledger entries
            income_type = 'Credit' if vtype == 'Service Income' else 'Debit'
            lines = [{'item_name': le['ledger_name'], 'quantity': 1,
                      'unit_price': le['amount'], 'amount': le['amount']}
                     for le in ledger_entries
                     if le['ledger_name'] not in SYSTEM_PRINT_LEDGERS and le['type'] == income_type and le['ledger_name'] != party]
        else:
            lines = item_entries
        from database import get_party_details
        party_details = get_party_details(ledger_name=party) if party else None
        subtotal = round(sum(l['amount'] or 0 for l in lines), 2)
        vat_applicable = bool(company.get('vat_applicable')) and vat_total > 0
        is_credit_note = vtype in ("Sales Return", "Service Income Return")
        doc_title = ("TAX CREDIT NOTE" if vat_applicable else "CREDIT NOTE") if is_credit_note \
            else ("TAX INVOICE" if vat_applicable else "INVOICE")
        return render_template(
            "print_tax_invoice.html",
            doc_title=doc_title,
            voucher=voucher_data,
            company=company,
            party=party,
            party_details=party_details,
            lines=lines,
            subtotal=subtotal,
            discount_total=round(discount_total, 2),
            vat_total=round(vat_total, 2),
            grand_total=round(subtotal + vat_total - discount_total, 2),
            is_credit_note=is_credit_note,
            username=current_user.username,
        )

    if vtype in ("Purchase", "Purchase Return"):
        vat_total = sum(le['amount'] or 0 for le in ledger_entries if le['ledger_name'] == 'Input VAT 5%')
        discount_total = sum(le['amount'] or 0 for le in ledger_entries if le['ledger_name'] in ('Discount Allowed', 'Discount Received'))
        party = next((le['ledger_name'] for le in ledger_entries
                      if le['ledger_name'] not in SYSTEM_PRINT_LEDGERS
                      and le['type'] == ('Credit' if vtype == 'Purchase' else 'Debit')), '')
        subtotal = round(sum(ie['amount'] or 0 for ie in item_entries), 2)
        from database import get_party_details
        party_details = get_party_details(ledger_name=party) if party else None
        return render_template(
            "print_purchase_voucher.html",
            voucher=voucher_data,
            company=company,
            party=party,
            party_details=party_details,
            lines=item_entries,
            subtotal=subtotal,
            discount_total=round(discount_total, 2),
            vat_total=round(vat_total, 2),
            grand_total=round(subtotal + vat_total - discount_total, 2),
            username=current_user.username,
        )

    # Everything else: journal voucher print
    return redirect(url_for("print_bp.print_jv", voucher_number=voucher_number))


@print_bp.route("/print/jv", methods=["GET", "POST"])
@login_required
def print_jv():
    voucher_number = request.args.get("voucher_number") or request.form.get("voucher_number")
    
    if not voucher_number:
        return render_template("print_jv_search.html", username=current_user.username)
    
    # Normalize voucher number (optional, but good for UX)
    voucher_number = voucher_number.strip()

    try:
        voucher_data, ledger_entries, item_entries = _fetch_voucher_for_print(voucher_number)

        if not voucher_data:
            flash(f"Voucher {voucher_number} not found", "error")
            return render_template("print_jv_search.html", username=current_user.username)

        # Filter out item entries for specific voucher types as requested
        # "From Sales, Purchase, Sales return, Purchase return and Stock adjustment remove the stock item line from print"
        excluded_types = ["Sales", "Purchase", "Sales Return", "Purchase Return", "Stock Adjustment"]
        if voucher_data["voucher_type"] in excluded_types:
            item_entries = []

        return render_template(
            "print_jv_view.html",
            voucher=voucher_data,
            ledger_entries=ledger_entries,
            item_entries=item_entries,
            username=current_user.username
        )

    except Exception as e:
        print(f"Error fetching voucher for print: {str(e)}")
        flash("Database error occurred", "error")
        return render_template("print_jv_search.html", username=current_user.username)
