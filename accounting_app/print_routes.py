from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user
from database import get_connection, get_current_company_id
from .models import format_date

print_bp = Blueprint("print_bp", __name__)

@print_bp.route("/print/jv", methods=["GET", "POST"])
@login_required
def print_jv():
    voucher_number = request.args.get("voucher_number") or request.form.get("voucher_number")
    
    if not voucher_number:
        return render_template("print_jv_search.html", username=current_user.username)
    
    # Normalize voucher number (optional, but good for UX)
    voucher_number = voucher_number.strip()

    try:
        company_id = get_current_company_id()
        conn = get_connection()
        cursor = conn.cursor()

        def _val(row, key, index):
            return row[key] if isinstance(row, dict) or hasattr(row, 'keys') else row[index]

        # Fetch voucher header
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
            conn.close()
            flash(f"Voucher {voucher_number} not found", "error")
            return render_template("print_jv_search.html", username=current_user.username)

        # Use the actual voucher number from DB to ensure consistency in subsequent queries
        db_voucher_number = _val(voucher, 'voucher_number', 0)

        voucher_data = {
            "voucher_number": _val(voucher, 'voucher_number', 0),
            "voucher_type": _val(voucher, 'voucher_type', 1),
            "date": format_date(_val(voucher, 'date', 2)),
            "cost_center_code": _val(voucher, 'cost_center_code', 3),
            "narration": _val(voucher, 'narration', 4),
            "location_name": _val(voucher, 'location_name', 5)
        }

        # Fetch cost center name if applicable
        if voucher_data["cost_center_code"]:
            cursor.execute(
                "SELECT center_name FROM cost_centers WHERE center_code = %s AND company_id = %s",
                (voucher_data["cost_center_code"], company_id),
            )
            result = cursor.fetchone()
            voucher_data["cost_center_name"] = _val(result, 'center_name', 0) if result else None

        # Fetch ledger entries
        cursor.execute(
            """
            SELECT ledger_name, amount, type
            FROM ledger_entries
            WHERE voucher_number = %s AND company_id = %s
            ORDER BY type DESC  -- Debit first usually
            """,
            (db_voucher_number, company_id),
        )
        ledger_entries_rows = cursor.fetchall()
        ledger_entries = [
            {"ledger_name": _val(row, 'ledger_name', 0), "amount": _val(row, 'amount', 1), "type": _val(row, 'type', 2)} 
            for row in ledger_entries_rows
        ]

        # Fetch item entries
        cursor.execute(
            """
            SELECT item_name, quantity, unit_price, amount, ledger_name, type, location_name
            FROM item_entries
            WHERE voucher_number = %s AND company_id = %s
            """,
            (db_voucher_number, company_id),
        )
        item_entries_rows = cursor.fetchall()
        item_entries = [
            {
                "item_name": _val(row, 'item_name', 0), 
                "quantity": _val(row, 'quantity', 1), 
                "unit_price": _val(row, 'unit_price', 2), 
                "amount": _val(row, 'amount', 3), 
                "ledger_name": _val(row, 'ledger_name', 4), 
                "type": _val(row, 'type', 5),
                "location_name": _val(row, 'location_name', 6)
            }
            for row in item_entries_rows
        ]

        # Filter out item entries for specific voucher types as requested
        # "From Sales, Purchase, Sales return, Purchase return and Stock adjustment remove the stock item line from print"
        excluded_types = ["Sales", "Purchase", "Sales Return", "Purchase Return", "Stock Adjustment"]
        if voucher_data["voucher_type"] in excluded_types:
            item_entries = []
        
        conn.close()

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
