from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
import sqlite3
import json

from database import (
    get_ledgers,
    get_items,
    get_cost_centers,
    get_inventory_details,
    get_stock_movement_data,
    add_voucher,
    add_additional_charges_voucher,
    calculate_weighted_average_price,
    get_company_settings,
    get_locations,
    get_default_location,
    validate_return_quantity,
    get_current_stock,
    get_voucher_details,
    update_voucher_entries,
    get_current_company_id,
)

from .models import (
    format_date,
    parse_date,
    get_sales_group_code,
    get_purchase_group_code,
    validate_voucher_ledger_groups,
)

from database.voucher_config_db import get_voucher_config, get_allowed_ledgers

from . import get_db_connection

voucher_bp = Blueprint("voucher_bp", __name__)

COST_CENTER_ALLOWED_TYPES = {
    "Journal",
    "Expense",
    "Sales",
    "Sales Return",
    "Service Income",
    "Service Income Return",
    "Purchase",
    "Purchase Return",
    "Stock Adjustment",
}


def _normalize_voucher_type(vt):
    mapping = {
        "receipt": "Receipt",
        "payment": "Payment",
        "contra": "Contra",
        "journal": "Journal",
        "expense": "Expense",
        "sales": "Sales",
        "sales_return": "Sales Return",
        "service_income": "Service Income",
        "service_income_return": "Service Income Return",
        "purchase": "Purchase",
        "purchase_return": "Purchase Return",
        "physical_stock": "Stock Adjustment",
        "stock_adjustment": "Stock Adjustment",
        "inventory_transfer": "Inventory Transfer",
        "additional_charges": "Additional Charge",
    }
    return mapping.get(vt, vt)


@voucher_bp.route("/api/search_purchase_vouchers", methods=["GET"])
@login_required
def search_purchase_vouchers():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"success": True, "results": []})
    
    try:
        company_id = get_current_company_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Search by Voucher Number OR Original Reference
        sql = """
            SELECT v.voucher_number, v.date, MAX(l.ledger_name), v.amount, v.original_invoice_ref, v.original_invoice_date
            FROM vouchers v
            JOIN ledger_entries l ON v.company_id = l.company_id AND v.voucher_number = l.voucher_number
            WHERE v.voucher_type = 'Purchase' 
            AND l.type = 'Credit'
            AND v.company_id = ?
            AND (v.voucher_number LIKE ? OR COALESCE(v.original_invoice_ref, '') LIKE ?)
            GROUP BY v.voucher_number, v.date, v.amount, v.original_invoice_ref, v.original_invoice_date
            ORDER BY v.date DESC
            LIMIT 20
        """
        search_term = f"%{query}%"
        cursor.execute(sql, (company_id, search_term, search_term))
        rows = cursor.fetchall()
        
        results = []
        for r in rows:
            results.append({
                "voucher_number": r[0],
                "date": format_date(r[1]),
                "party_name": r[2],
                "amount": r[3],
                "ref": r[4] or "",
                "ref_date": format_date(r[5]) if r[5] else ""
            })
            
        conn.close()
        return jsonify({"success": True, "results": results})
        
    except Exception as e:
        print(f"Error searching purchase vouchers: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@voucher_bp.route("/api/voucher_assistant_bootstrap", methods=["GET"])
@login_required
def voucher_assistant_bootstrap():
    try:
        ledgers = get_ledgers()
        cost_centers = get_cost_centers(active_only=True)
        company = get_company_settings() or {}

        return jsonify(
                {
                    "success": True,
                    "ledgers": [{"name": l['ledger_name'], "group_code": l['group_code'], "balance": l['closing_balance']} for l in (ledgers or []) if l.get('ledger_name')],
                    "cost_centers": [c['center_name'] for c in (cost_centers or []) if c.get('center_name')],
                "vat_applicable": bool(company.get("vat_applicable") == 1),
                "cost_center_applicable": bool(company.get("cost_center_applicable")),
                "cost_center_mandatory": bool(company.get("cost_center_mandatory")),
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500





@voucher_bp.route("/vouchers")
@login_required
def vouchers():
    company = get_company_settings() or {}
    inventory_applicable = bool(company.get("inventory_applicable") == 1)
    
    return render_template(
        "vouchers_dashboard.html",
        inventory_applicable=inventory_applicable
    )


@voucher_bp.route("/voucher/<voucher_type>")
@login_required
def voucher(voucher_type):
    voucher_type = _normalize_voucher_type(voucher_type)
    company_id = get_current_company_id()
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Edit mode handling
        voucher_number_param = request.args.get("voucher_number")
        edit_mode = bool(voucher_number_param)
        existing_voucher_data = None
        ledger_entries = []
        edit_voucher_number = None
        edit_date = None
        edit_narration = ""
        edit_cost_center_name = None
        
        if edit_mode:
            print(f"DEBUG: Edit mode ACTIVATED for voucher: {voucher_number_param}")
            try:
                existing_voucher_data = get_voucher_details(voucher_number_param)
                print(f"DEBUG: Got voucher data: {existing_voucher_data}")
                edit_voucher_number = existing_voucher_data["header"]["voucher_number"]
                edit_date = existing_voucher_data["header"]["date"]
                edit_narration = existing_voucher_data["header"].get("narration", "")
                edit_cost_center_name = existing_voucher_data["header"].get("cost_center_name")
                # ledger_entries is a list of dicts from get_voucher_details
                ledger_entries = existing_voucher_data.get("ledger_entries", [])
                print(f"DEBUG: Edit mode - ledger_entries count: {len(ledger_entries)}, data: {ledger_entries}")
            except ValueError as e:
                print(f"DEBUG: ValueError in edit mode: {e}")
                flash(str(e), "error")
                return redirect(url_for("voucher_bp.edit_voucher_search"))
            except Exception as e:
                print(f"DEBUG: Exception in edit mode: {e}")
                raise

        # Normal voucher entry
        ledgers = get_ledgers(company_id=company_id)
        items = get_items(company_id=company_id)

        
        # Specific logic for Additional Charges
        # purchase_vouchers = [] - Removed to load via API
        purchase_vouchers = []
        # if voucher_type == "Additional Charge":
        #     cursor.execute("""
        #         SELECT v.voucher_number, v.date, l.ledger_name, v.amount, v.original_invoice_ref, v.original_invoice_date
        #         FROM vouchers v
        #         JOIN ledger_entries l ON v.voucher_number = l.voucher_number
        #         WHERE v.voucher_type = 'Purchase' AND l.type = 'Credit'
        #         GROUP BY v.voucher_number
        #         ORDER BY v.date DESC
        #     """)
        #     purchase_vouchers = cursor.fetchall()
            
        # For new vouchers, only show active cost centers
        cost_centers = get_cost_centers(active_only=True, company_id=company_id)
        inventory_details = get_inventory_details(company_id=company_id)
        company = get_company_settings(company_id=company_id)
        multiple_locations_enabled = bool(
            company and company.get("multiple_locations_applicable")
        )
        cost_center_applicable = bool(
            company 
            and company.get("cost_center_applicable") 
            and voucher_type in COST_CENTER_ALLOWED_TYPES
        )
        cost_center_mandatory = bool(
            company 
            and company.get("cost_center_mandatory")
            and cost_center_applicable
        )
        locations = get_locations(company_id=company_id) if multiple_locations_enabled else []

        items_dict = {
            item['name']: {
                "unit_price": item['unit_price'],
                "unit_code": item['unit_code'],
                "vat_rate": item['vat_rate'],
            }
            for item in inventory_details
        }

        result_message = request.args.get("message", "")
        sales_group_code = get_sales_group_code(company_id=company_id)
        purchase_group_code = get_purchase_group_code(company_id=company_id)
        
        # Helper to treat all as allowed if empty, or just pass strictly what is allowed.
        # DB logic returns ALL if config is missing.
        # We need serialized lists for JS.
        try:
            from database.voucher_config_db import get_allowed_ledgers
            allowed_dr = get_allowed_ledgers(voucher_type, "Debit", company_id=company_id)
            allowed_cr = get_allowed_ledgers(voucher_type, "Credit", company_id=company_id)
        except ImportError:
             allowed_dr = [] 
             allowed_cr = []

        # Convert to list of ledger names or codes to pass to JS
        # We probably want names since the dropdown values are names usually? 
        # Check template. Usually value="{{ ledger[1] }}".
        # get_allowed_ledgers returns dicts with 'name', 'code'.
        allowed_ledgers_dr = [l["name"] for l in allowed_dr]
        allowed_ledgers_cr = [l["name"] for l in allowed_cr]

        # Group code checks
        if (
            voucher_type in ["Sales", "Sales Return", "Service Income"]
            and not sales_group_code
        ) or (
            voucher_type in ["Purchase", "Purchase Return"]
            and not purchase_group_code
        ):
            conn.close()
            print(f"Error: Missing group code for {voucher_type}")
            msg = (
                "Error: Required groups (Sales/Purchase) not found. "
                "Please add them first."
            )
            # show as flash error
            flash(msg, "error")
            return render_template(
                "voucher.html",
                voucher_type=voucher_type,
                ledgers=ledgers,
                items=items,
                items_dict=items_dict,
                cost_centers=cost_centers,
                result_message=msg,
                sales_group_code=None,
                purchase_group_code=None,
                edit_mode=False,
                multiple_locations_enabled=multiple_locations_enabled,
                cost_center_applicable=cost_center_applicable,
                cost_center_mandatory=cost_center_mandatory,
                locations=locations,
                selected_location_name=None,
                username=current_user.username,
            )

        conn.close()
        print(
            f"Voucher {voucher_type} loaded: "
            f"ledgers={len(ledgers)}, items={len(items)}"
        )
        return render_template(
            "voucher.html",
            voucher_type=voucher_type,
            ledgers=ledgers,
            items=items,
            items_dict=items_dict,
            cost_centers=cost_centers,
            result_message=result_message,
            sales_group_code=sales_group_code,
            purchase_group_code=purchase_group_code,
            edit_mode=edit_mode,
            ledger_entries=ledger_entries,
            edit_voucher_number=edit_voucher_number,
            edit_date=edit_date,
            edit_narration=edit_narration,
            edit_cost_center_name=edit_cost_center_name,
            multiple_locations_enabled=multiple_locations_enabled,
            cost_center_applicable=cost_center_applicable,
            cost_center_mandatory=cost_center_mandatory,
            locations=locations,
            selected_location_name=None,
            username=current_user.username,
            purchase_vouchers=purchase_vouchers,
            allowed_ledgers_dr=allowed_ledgers_dr,
            allowed_ledgers_cr=allowed_ledgers_cr,
        )
    except Exception as e:
        print(f"Error in voucher: {str(e)}")
        return render_template(
            "error.html", error="Database unavailable"
        ), 500





@voucher_bp.route("/api/voucher_items_for_return", methods=["GET"])
@login_required
def api_voucher_items_for_return():
    voucher_number = request.args.get("voucher_number")
    target_type = _normalize_voucher_type(request.args.get("target_type") or "")
    if not voucher_number or not target_type:
        return jsonify({"success": False, "message": "voucher_number and target_type are required"}), 400
    try:
        company_id = get_current_company_id()
        if not company_id:
            return jsonify({"success": False, "message": "No company selected"}), 400
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT voucher_type, location_name FROM vouchers WHERE voucher_number = ? AND company_id = ?", (voucher_number, company_id))
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "Source voucher not found"}), 404
        source_type, source_location = row[0], row[1]
        if target_type == "Sales Return" and source_type != "Sales":
            conn.close()
            return jsonify({"success": False, "message": "Select a Sales voucher for Sales Return"}), 400
        if target_type == "Purchase Return" and source_type != "Purchase":
            conn.close()
            return jsonify({"success": False, "message": "Select a Purchase voucher for Purchase Return"}), 400
        if target_type == "Service Income Return" and source_type != "Service Income":
            conn.close()
            return jsonify({"success": False, "message": "Select a Service Income voucher for Service Income Return"}), 400
        cur.execute("""
            SELECT item_name, quantity, unit_price, amount, ledger_name, type, COALESCE(location_name, '')
            FROM item_entries WHERE voucher_number = ? AND company_id = ?
        """, (voucher_number, company_id))
        items = cur.fetchall()
        cur.execute("""
            SELECT ledger_name, amount, type
            FROM ledger_entries WHERE voucher_number = ? AND company_id = ?
        """, (voucher_number, company_id))
        ledgers_src = cur.fetchall()
        conn.close()
        reversed_items = []
        fixed_type = "Debit" if target_type in ["Sales Return", "Service Income Return"] else "Credit"
        for item_name, qty, unit_price, amount, ledger_name, type_, loc in items:
            qty_f = float(qty or 0)
            rate = float(unit_price or 0)  # use original voucher unit price
            line_amount = round(qty_f * rate, 2)
            reversed_items.append({
                "item_name": item_name,
                "quantity": qty_f,
                "unit_price": rate,
                "amount": line_amount,
                "ledger_name": ledger_name,
                "type": fixed_type,
                "location_name": loc or source_location or "Main Location",
            })
        # Collect ledgers used by items to exclude them from explicit ledger entries
        item_ledgers = set(i[4] for i in items)

        reversed_ledgers = []
        for ledger_name, amount, type_ in ledgers_src:
            if ledger_name in ("Output VAT 5%", "Input VAT 5%"):
                continue
            if target_type == "Sales Return" and ledger_name in ("Cost of Goods Sold", "Inventory"):
                continue
            if target_type == "Purchase Return" and ledger_name == "Inventory":
                continue
            # Also filter out any ledger that is associated with items (e.g., Sales Account, Purchase Account)
            # because the items themselves will regenerate these postings.
            if ledger_name in item_ledgers:
                continue

            reversed_ledgers.append({
                "ledger_name": ledger_name,
                "amount": float(amount or 0),
                "type": "Debit" if type_ == "Credit" else "Credit",
            })
        return jsonify({"success": True, "items": reversed_items, "ledgers": reversed_ledgers})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@voucher_bp.route("/api/item_available_qty", methods=["GET"])
@login_required
def api_item_available_qty():
    item_name = request.args.get("item_name")
    location_name = request.args.get("location_name") or "Main Location"
    date_str = request.args.get("date")
    if not item_name:
        return jsonify({"success": False, "message": "item_name is required"}), 400
    try:
        qty = get_current_stock(item_name, location_name, date=date_str)
        return jsonify({"success": True, "qty": round(qty, 2)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@voucher_bp.route("/add_voucher", methods=["POST"])
@login_required
def add_voucher_route():
    company_id = get_current_company_id()
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    voucher_type = _normalize_voucher_type(request.form["voucher_type"])
    date = parse_date(request.form["date"])
    cost_center_name = request.form.get("cost_center_name")
    narration = request.form.get("narration", "")
    location_name_form = request.form.get("location_name")
    from_location_name_form = request.form.get("from_location_name")
    to_location_name_form = request.form.get("to_location_name")
    
    # Additional Charge Handling
    if voucher_type == "Additional Charge":
        try:
            linked_voucher_number = request.form.get("linked_voucher_number")
            
            charge_party_ledgers = request.form.getlist("charge_party_ledger[]")
            charge_amounts = request.form.getlist("charge_amount[]")
            charge_vat_amounts = request.form.getlist("charge_vat_amount[]")
            charge_methods = request.form.getlist("charge_method[]")
            charge_narrations = request.form.getlist("charge_narration[]")
            
            if not linked_voucher_number:
                 raise ValueError("Linked Purchase Voucher is required")
                 
            charges = []
            
            # Ensure charge_vat_amounts matches length (pad with 0 if missing, though HTML should send it)
            if len(charge_vat_amounts) < len(charge_amounts):
                charge_vat_amounts.extend(['0'] * (len(charge_amounts) - len(charge_vat_amounts)))

            for pl, amt, vat_amt, method, c_narr in zip(charge_party_ledgers, charge_amounts, charge_vat_amounts, charge_methods, charge_narrations):
                 if not pl:
                     raise ValueError("Party Ledger is required for all charges")
                 charges.append({
                     'party_ledger': pl,
                     'amount': float(amt),
                     'vat_amount': float(vat_amt) if vat_amt else 0.0,
                     'valuation_method': method,
                     'narration': c_narr
                 })
            
            if not charges:
                raise ValueError("At least one charge line is required")

            v_no = add_additional_charges_voucher(date, linked_voucher_number, charges, narration)
            
            if is_ajax:
                return jsonify({"success": True, "message": "Voucher added successfully", "voucher_number": v_no})
            flash(f"Voucher {v_no} added successfully", "success")
            return redirect(url_for("voucher_bp.vouchers"))
            
        except Exception as e:
            print(f"Error adding Additional Charge voucher: {str(e)}")
            if is_ajax:
                return jsonify({"success": False, "message": str(e)}), 400
            flash(str(e), "error")
            return redirect(url_for("voucher_bp.voucher", voucher_type=voucher_type))

    if voucher_type == "Inventory Transfer" and (not from_location_name_form or not to_location_name_form):
        msg = "Both From Location and To Location are required for Inventory Transfer"
        if is_ajax:
            return jsonify({"success": False, "message": msg}), 400
        flash(msg, "error")
        return redirect(url_for("voucher_bp.voucher", voucher_type=voucher_type))

    company = get_company_settings(company_id=company_id)
    multiple_locations_enabled = bool(
        company and company.get("multiple_locations_applicable")
    )
    default_loc = get_default_location(company_id=company_id)
    if multiple_locations_enabled:
        final_location_name = (
            location_name_form or (default_loc['location_name'] if default_loc else "Main Location")
        )
    else:
        final_location_name = "Main Location"

    cost_center_code = None
    if cost_center_name:
        cost_centers = get_cost_centers(company_id=company_id)
        cost_center_map = {c['center_name']: c['center_code'] for c in cost_centers}
        cost_center_code = cost_center_map.get(cost_center_name)

    ledger_names = request.form.getlist("ledger_name[]")
    ledger_amounts = request.form.getlist("ledger_amount[]")
    ledger_types = request.form.getlist("ledger_type[]")
    ledger_cost_centers = request.form.getlist("ledger_cost_center[]")

    if not ledger_names and voucher_type not in ("Physical Stock", "Inventory Transfer", "Stock Adjustment"):
        print(f"No ledger entries for {voucher_type}")
        if is_ajax:
            return jsonify({"success": False, "message": "At least one ledger entry is required"}), 400
        flash("At least one ledger entry is required", "error")
        return redirect(
            url_for("voucher_bp.voucher", voucher_type=voucher_type)
        )

    try:
        # Pad cost centers if they are missing (e.g. if not applicable)
        if len(ledger_cost_centers) < len(ledger_names):
            ledger_cost_centers = [None] * len(ledger_names)

        ledger_entries = [
            {
                "ledger_name": name,
                "amount": round(float(amount), 2),
                "type": type_,
                "cost_center_code": (
                    {c['center_name']: c['center_code'] for c in get_cost_centers(company_id=company_id)}.get(cc_name) 
                    if cc_name else None
                )
            }
            for name, amount, type_, cc_name in zip(
                ledger_names, ledger_amounts, ledger_types, ledger_cost_centers
            )
        ]
        print(f"Ledger entries for {voucher_type}: {ledger_entries}")
    except ValueError as e:
        print(f"Error in ledger entries: {str(e)}")
        if is_ajax:
            return jsonify({"success": False, "message": f"Invalid amount in ledger entries: {str(e)}"}), 400
        flash("Invalid amount in ledger entries", "error")
        return redirect(
            url_for("voucher_bp.voucher", voucher_type=voucher_type)
        )

    is_valid, err = validate_voucher_ledger_groups(voucher_type, ledger_entries, company_id=company_id)
    if voucher_type != "Stock Adjustment" and not is_valid:
        print(f"Voucher validation failed: {err}")
        if is_ajax:
            return jsonify({"success": False, "message": err}), 400
        flash(err, "error")
        return redirect(
            url_for("voucher_bp.voucher", voucher_type=voucher_type)
        )

    # Item entries (exclude Service Income)
    item_entries = []
    vat_amounts_items = []
    if voucher_type in [
        "Sales",
        "Sales Return",
        "Purchase",
        "Purchase Return",
        "Stock Adjustment",
        "Inventory Transfer",
    ]:
        item_names = request.form.getlist("item_name[]")
        quantities = request.form.getlist("quantity[]")
        unit_prices = request.form.getlist("unit_price[]")
        item_amounts = request.form.getlist("item_amount[]")
        item_ledger_names = request.form.getlist("item_ledger_name[]")
        item_types = request.form.getlist("item_type[]")
        vat_percents = request.form.getlist("vat_percent[]")
        vat_amounts_items = request.form.getlist("vat_amount[]")
        ref_voucher_numbers = request.form.getlist("ref_voucher_number[]")
        item_cost_centers = request.form.getlist("item_cost_center[]")
        item_weights = request.form.getlist("weight_kg[]")

        # Pre-fetch cost centers map
        cc_map = {c['center_name']: c['center_code'] for c in get_cost_centers()}

        if item_names:
            # Pad cost centers if needed
            if len(item_cost_centers) < len(item_names):
                item_cost_centers = [None] * len(item_names)
            # Pad weights if needed (e.g. if not provided in form)
            if len(item_weights) < len(item_names):
                item_weights = [0.0] * len(item_names)

            try:
                if voucher_type == "Stock Adjustment":
                    item_entries_raw = [
                        {
                            "item_name": name,
                            "quantity": float(qty),
                            "unit_price": 0.0,
                            "amount": 0.0,
                            "ledger_name": ledger_name,
                            "type": type_,
                            "cost_center_code": cc_map.get(cc_name) if cc_name else None,
                        }
                        for name, qty, ledger_name, type_, cc_name in zip(
                            item_names,
                            quantities,
                            item_ledger_names,
                            item_types,
                            item_cost_centers,
                        )
                    ]
                elif voucher_type == "Inventory Transfer":
                    item_entries_raw = [
                        {
                            "item_name": name,
                            "quantity": float(qty),
                            "unit_price": 0.0,
                            "amount": 0.0,
                            "ledger_name": None,
                            "type": None,
                            "cost_center_code": None, # Not typically used in transfer but could be
                        }
                        for name, qty in zip(item_names, quantities)
                    ]
                else:
                    item_entries_raw = []
                    for i, (name, qty, price, amount, ledger_name, type_, cc_name, weight) in enumerate(zip(
                        item_names,
                        quantities,
                        unit_prices,
                        item_amounts,
                        item_ledger_names,
                        item_types,
                        item_cost_centers,
                        item_weights
                    )):
                        item_entries_raw.append({
                        "item_name": name,
                        "quantity": float(qty),
                        "unit_price": round(float(price), 2),
                        "amount": round(float(amount), 2),
                        "ledger_name": ledger_name,
                        "type": type_,
                        "cost_center_code": cc_map.get(cc_name) if cc_name else None,
                        "weight_kg": float(weight) if weight else 0.0,
                        "_ref_voucher_number": (ref_voucher_numbers[i] if i < len(ref_voucher_numbers) else None),
                        })
                item_entries = item_entries_raw
                
                # Validate return quantities
                for entry in item_entries:
                    if voucher_type in ["Sales Return", "Purchase Return"] and not entry.get("_ref_voucher_number"):
                        msg = f"Source voucher number is required for {voucher_type} item '{entry['item_name']}'"
                        if is_ajax:
                            return jsonify({"success": False, "message": msg}), 400
                        flash(msg, "error")
                        return redirect(url_for("voucher_bp.voucher", voucher_type=voucher_type))

                    if entry.get("_ref_voucher_number"):
                        valid, msg = validate_return_quantity(
                            entry["_ref_voucher_number"],
                            entry["item_name"],
                            entry["quantity"]
                        )
                        if not valid:
                            if is_ajax:
                                return jsonify({"success": False, "message": msg}), 400
                            flash(msg, "error")
                            return redirect(url_for("voucher_bp.voucher", voucher_type=voucher_type))

                if voucher_type == "Inventory Transfer":
                    # Compute WAP per item and create mirrored entries for from/to locations
                    from_loc = from_location_name_form
                    to_loc = to_location_name_form
                    from database import calculate_weighted_average_price
                    item_entries = []
                    for ie in item_entries_raw:
                        wap = calculate_weighted_average_price(ie["item_name"], date)
                        qty = ie["quantity"]
                        amount_wap = round(qty * wap, 2)
                        # Credit from source location
                        item_entries.append({
                            "item_name": ie["item_name"],
                            "quantity": qty,
                            "unit_price": wap,
                            "amount": amount_wap,
                            "ledger_name": ie.get("ledger_name"),
                            "type": "Credit",
                            "_location_override": from_loc,
                        })
                        # Debit to destination location
                        item_entries.append({
                            "item_name": ie["item_name"],
                            "quantity": qty,
                            "unit_price": wap,
                            "amount": amount_wap,
                            "ledger_name": ie.get("ledger_name"),
                            "type": "Debit",
                            "_location_override": to_loc,
                        })
                elif voucher_type == "Stock Adjustment":
                    from database import calculate_weighted_average_price
                    # Build ledger side from user choice; stock side via item_entries opposite type
                    ledger_entries = []
                    adjusted_item_entries = []
                    for ie in item_entries_raw:
                        wap = calculate_weighted_average_price(ie["item_name"], date)
                        qty = float(ie["quantity"])  # can be +/-
                        amount_wap = round(abs(qty) * wap, 2)
                        chosen_type = ie.get("type") or "Debit"
                        opposite_type = "Credit" if chosen_type == "Debit" else "Debit"
                        # Selected ledger as chosen by user
                        ledger_entries.append({
                            "ledger_name": ie.get("ledger_name"),
                            "amount": amount_wap,
                            "type": chosen_type,
                        })
                        # Stock (closing inventory) opposite; carry quantity sign to affect inventory
                        adjusted_item_entries.append({
                            "item_name": ie["item_name"],
                            "quantity": qty,
                            "unit_price": wap,
                            "amount": amount_wap,
                            "ledger_name": None,
                            "type": opposite_type,
                        })
                    item_entries = adjusted_item_entries
                if voucher_type in [
                    "Sales",
                    "Sales Return",
                    "Purchase",
                    "Purchase Return",
                ]:
                    fixed_type = None
                    if voucher_type == "Sales":
                        fixed_type = "Credit"
                    elif voucher_type == "Sales Return":
                        fixed_type = "Debit"
                    elif voucher_type == "Purchase":
                        fixed_type = "Debit"
                    elif voucher_type == "Purchase Return":
                        fixed_type = "Credit"
                    if fixed_type:
                        for ie in item_entries:
                            ie["type"] = fixed_type
                print(
                    f"Item entries for {voucher_type}: {item_entries}"
                )

                # Validate stock availability (SKIPPED per user request)
                if voucher_type in ["Sales", "Stock Adjustment", "Inventory Transfer", "Purchase Return"]:
                    pass
                    # Logic disabled to allow negative stock / simplified entry
                    # Check logic kept in comments or removed


            except ValueError as e:
                print(f"Error in item entries: {str(e)}")
                if is_ajax:
                    return jsonify({"success": False, "message": f"Invalid amount or quantity in item entries: {str(e)}"}), 400
                flash("Invalid amount or quantity in item entries", "error")
                return redirect(
                    url_for("voucher_bp.voucher", voucher_type=voucher_type)
                )

    # Mandatory Cost Center Check
    if company.get("cost_center_applicable") and company.get("cost_center_mandatory") and voucher_type in COST_CENTER_ALLOWED_TYPES:
        # If Header CC is missing, check if relevant lines have it
        if not cost_center_code:
            missing_cc = False
            # Inventory Vouchers -> Check Item Entries
            if voucher_type in ["Sales", "Purchase", "Sales Return", "Purchase Return", "Stock Adjustment"]:
                for ie in item_entries:
                    if not ie.get("cost_center_code"):
                        missing_cc = True
                        break
            # Accounting Vouchers -> Check Ledger Entries
            # (Exclude VAT/System entries if any, but at this stage ledger_entries are mostly user input)
            elif voucher_type in ["Expense", "Journal", "Service Income", "Service Income Return"]:
                for le in ledger_entries:
                    if not le.get("cost_center_code"):
                        missing_cc = True
                        break
            
            if missing_cc:
                msg = "Cost Center is mandatory. Please select a Cost Center in the Header or for every Line."
                print(f"Mandatory CC check failed for {voucher_type}")
                if is_ajax:
                    return jsonify({"success": False, "message": msg}), 400
                flash(msg, "error")
                return redirect(url_for("voucher_bp.voucher", voucher_type=voucher_type))

    # VAT injection
    try:
        total_vat_items = round(sum(float(v or 0) for v in vat_amounts_items), 2)
    except ValueError:
        total_vat_items = 0.0

    if voucher_type in ["Sales", "Sales Return", "Purchase", "Purchase Return"] and total_vat_items != 0:
        vat_ledger_name = None
        vat_type = None

        if voucher_type == "Sales":
            vat_ledger_name = "Output VAT 5%"
            vat_type = "Credit"
        elif voucher_type == "Sales Return":
            vat_ledger_name = "Output VAT 5%"
            vat_type = "Debit"
        elif voucher_type == "Purchase":
            vat_ledger_name = "Input VAT 5%"
            vat_type = "Debit"
        elif voucher_type == "Purchase Return":
            vat_ledger_name = "Input VAT 5%"
            vat_type = "Credit"

        if vat_ledger_name and vat_type:
            ledger_entries.append(
                {
                    "ledger_name": vat_ledger_name,
                    "amount": total_vat_items,
                    "type": vat_type,
                }
            )
            print(
                f"Injected item VAT ledger: {vat_ledger_name} {vat_type} {total_vat_items}"
            )

    # Expense VAT (Journal vouchers never carry VAT; use Expense for VAT entries)
    if voucher_type == "Expense":
        ledger_vat_applicable = request.form.getlist("ledger_vat_applicable[]")
        ledger_vat_amounts = request.form.getlist("ledger_vat_amount[]")
        total_input_vat = 0.0

        for name, amount, type_, vat_app, vat_amt in zip(
            ledger_names,
            ledger_amounts,
            ledger_types,
            ledger_vat_applicable,
            ledger_vat_amounts,
        ):
            if type_ == "Debit" and vat_app in ("1", "Yes", "Y", "true", "True"):
                try:
                    total_input_vat += float(vat_amt or 0)
                except ValueError:
                    pass

        if total_input_vat > 0:
            ledger_entries.append(
                {
                    "ledger_name": "Input VAT 5%",
                    "amount": round(total_input_vat, 2),
                    "type": "Debit",
                }
            )
            print(
                f"Injected expense VAT ledger: Input VAT 5% Debit {total_input_vat}"
            )

    # Service Income VAT
    if voucher_type == "Service Income":
        ledger_vat_applicable = request.form.getlist("ledger_vat_applicable[]")
        ledger_vat_amounts = request.form.getlist("ledger_vat_amount[]")
        total_output_vat = 0.0

        for name, amount, type_, vat_app, vat_amt in zip(
            ledger_names,
            ledger_amounts,
            ledger_types,
            ledger_vat_applicable,
            ledger_vat_amounts,
        ):
            if type_ == "Credit" and vat_app in ("1", "Yes", "Y", "true", "True"):
                try:
                    total_output_vat += float(vat_amt or 0)
                except ValueError:
                    pass

        if total_output_vat > 0:
            ledger_entries.append(
                {
                    "ledger_name": "Output VAT 5%",
                    "amount": round(total_output_vat, 2),
                    "type": "Credit",
                }
            )
            print(
                f"Injected service income VAT ledger: Output VAT 5% Credit {total_output_vat}"
            )

    # Service Income Return VAT
    if voucher_type == "Service Income Return":
        ledger_vat_applicable = request.form.getlist("ledger_vat_applicable[]")
        ledger_vat_amounts = request.form.getlist("ledger_vat_amount[]")
        total_output_vat_reversal = 0.0

        for name, amount, type_, vat_app, vat_amt in zip(
            ledger_names,
            ledger_amounts,
            ledger_types,
            ledger_vat_applicable,
            ledger_vat_amounts,
        ):
            if type_ == "Debit" and vat_app in ("1", "Yes", "Y", "true", "True"):
                try:
                    total_output_vat_reversal += float(vat_amt or 0)
                except ValueError:
                    pass

        if total_output_vat_reversal > 0:
            ledger_entries.append(
                {
                    "ledger_name": "Output VAT 5%",
                    "amount": round(total_output_vat_reversal, 2),
                    "type": "Debit",
                }
            )
            print(
                f"Injected service income return VAT ledger: Output VAT 5% Debit {total_output_vat_reversal}"
            )

    # Final debit/credit balance validation AFTER VAT injections
    if voucher_type != "Stock Adjustment":
        total_debit = 0.0
        total_credit = 0.0
        for e in ledger_entries:
            amt = float(e.get("amount", 0) or 0)
            if e.get("type") == "Debit":
                total_debit += amt
            elif e.get("type") == "Credit":
                total_credit += amt
        for ie in item_entries:
            amt = float(ie.get("amount", 0) or 0)
            t = ie.get("type")
            if t == "Debit":
                total_debit += amt
            elif t == "Credit":
                total_credit += amt
        if abs(total_debit - total_credit) > 0.01:
            msg = (
                f"Debit {total_debit:.2f} and Credit {total_credit:.2f} not matching"
            )
            print(f"Balance check failed: {msg}")
            if is_ajax:
                return jsonify({"success": False, "message": msg}), 400
            flash(msg, "error")
            return redirect(
                url_for("voucher_bp.voucher", voucher_type=voucher_type)
            )

    # --- NEW LOGIC: Fetch Credit Days from Master ---
    if voucher_type in ["Sales", "Purchase", "Expense"] and not request.form.get("credit_days"):
         # Note: We check request.form.get("credit_days") to allow override if we ever added it back, 
         # but currently it's removed from UI.
         pass # Logic will be inserted here
         
         target_type = "Debit" if voucher_type == "Sales" else "Credit"
         candidates = [e['ledger_name'] for e in ledger_entries if e.get('type') == target_type]
         
         computed_credit_days = 0
         
         if candidates:
             try:
                 from accounting_app import get_db_connection
                 conn_cd = get_db_connection()
                 cur_cd = conn_cd.cursor()
                 placeholders = ','.join('?' for _ in candidates)
                 query = f"SELECT credit_days FROM ledgers WHERE ledger_name IN ({placeholders}) AND credit_days > 0 LIMIT 1"
                 cur_cd.execute(query, candidates)
                 row = cur_cd.fetchone()
                 if row:
                     computed_credit_days = row[0]
                 conn_cd.close()
             except Exception as e:
                 print(f"Error fetching credit days: {e}")

         if computed_credit_days > 0:
             # Override the form variable effectively for the scope (not the request object)
             # Actually add_voucher takes 'credit_days' arg. 
             # We should set a local variable that is passed to add_voucher?
             # add_voucher usage in this file:
             # line 1001: credit_days=credit_days,
             # The existing code (lines 969-980) sets 'credit_days' variable.
             # If we set it here, the subsequent code (lines 969-980) might overwrite it?
             # Existing code: credit_days = None (line 970).
             # So we MUST insert AFTER the existing code resetting it.
             pass
    
    # Final posting
    try:
        # Calculate Due Date if Credit Days is present
        credit_days_str = request.form.get("credit_days")
        credit_days = None
        due_date = None
        if credit_days_str and voucher_type in ["Sales", "Purchase", "Expense"]:
            try:
                credit_days = int(credit_days_str)
                from datetime import datetime, timedelta
                voucher_date_obj = datetime.strptime(date, '%Y-%m-%d')
                due_date_obj = voucher_date_obj + timedelta(days=credit_days)
                due_date = due_date_obj.strftime('%Y-%m-%d')
            except ValueError:
                pass

        # For Inventory Transfer, we want to persist per-entry location overrides
        # We pass the combined location string into vouchers table for readability
        if voucher_type == "Inventory Transfer":
            final_location_name = f"{from_location_name_form or ''} -> {to_location_name_form or ''}"
            # Inject location override into entries during add_voucher via temporary monkey-patch
            # We will pass entries as-is; vouchers_db will read location_name from parameter
        
        # Original Invoice details (mandatory date for Purchase/Purchase Return/Expense)
        original_invoice_ref = request.form.get("original_invoice_ref")
        original_invoice_date = parse_date(request.form.get("original_invoice_date"))
        if voucher_type in ["Purchase", "Purchase Return", "Expense"] and not original_invoice_date:
            raise ValueError(f"Invoice Date is required for {voucher_type} vouchers")

        voucher_number = add_voucher(
            voucher_type,
            date,
            ledger_entries,
            item_entries,
            cost_center_code,
            narration=narration,
            location_name=final_location_name,
            credit_days=credit_days,
            due_date=due_date,
            original_invoice_ref=original_invoice_ref,
            original_invoice_date=original_invoice_date
        )
        print(
            f"Added voucher: {voucher_number} with narration: {narration}"
        )
        if is_ajax:
            return jsonify({"success": True, "message": f"Voucher Added with Reference {voucher_number}", "voucher_number": voucher_number})
        flash(f"Voucher Added with Reference {voucher_number}", "success")
        return redirect(
            url_for("voucher_bp.voucher", voucher_type=voucher_type)
        )
    except Exception as e:
        print(f"Error adding voucher: {str(e)}")
        if is_ajax:
            return jsonify({"success": False, "message": str(e)}), 500
        flash(str(e), "error")
        return redirect(
            url_for("voucher_bp.voucher", voucher_type=voucher_type)
        )








@voucher_bp.route("/api/get_voucher_details")
@login_required
def api_get_voucher_details():
    voucher_number = request.args.get("voucher_number")
    if not voucher_number:
        return jsonify({"success": False, "message": "Voucher number required"}), 400
    try:
        data = get_voucher_details(voucher_number)
        if data is None:
            return jsonify({"success": False, "message": "Voucher not found"}), 404
        return jsonify({"success": True, "data": data})
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400
    except Exception as e:
        print(f"Error fetching voucher details: {e}")
        return jsonify({"success": False, "message": "Internal Server Error"}), 500

@voucher_bp.route("/edit_voucher_search")
@login_required
def edit_voucher_search():
    return render_template("edit_voucher_search.html")



@voucher_bp.route("/update_voucher", methods=["POST"])
@login_required
def update_voucher_route():
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    try:
        voucher_number = request.form["voucher_number"]
        voucher_type = request.form["voucher_type"] # Should match db type
        date = parse_date(request.form["date"])
        narration = request.form.get("narration", "")
        cost_center_name = request.form.get("cost_center_name")
        
        # Resolve Cost Center Code
        cost_center_code = None
        if cost_center_name:
            # We need to resolve name to code again (or pass code from form)
            # Assuming get_cost_centers is available
            ccs = get_cost_centers()
            for code, name, _ in ccs:
                if name == cost_center_name:
                    cost_center_code = code
                    break
        
        # Parse Ledgers
        ledger_names = request.form.getlist("ledger_name[]")
        ledger_amounts = request.form.getlist("ledger_amount[]")
        ledger_types = request.form.getlist("ledger_type[]")
        ledger_cc_names = request.form.getlist("ledger_cost_center[]")
        
        # Validation
        if not ledger_names:
             raise ValueError("At least one ledger entry is required")

        ledger_entries = []
        # Pre-fetch CC map
        cc_map = {c[1]: c[0] for c in get_cost_centers()}
        
        # Pad CCs if missing
        if len(ledger_cc_names) < len(ledger_names):
            ledger_cc_names = [None] * len(ledger_names)

        for name, amt, ltype, lcc_name in zip(ledger_names, ledger_amounts, ledger_types, ledger_cc_names):
            ledger_entries.append({
                "ledger_name": name,
                "amount": round(float(amt), 2),
                "type": ltype,
                "cost_center_code": cc_map.get(lcc_name) if lcc_name else None
            })

        # --- VAT Injection Logic (Copied from add_voucher) ---
        # Expense VAT (Journal vouchers never carry VAT)
        if voucher_type == "Expense":
            ledger_vat_applicable = request.form.getlist("ledger_vat_applicable[]")
            ledger_vat_amounts = request.form.getlist("ledger_vat_amount[]")
            total_input_vat = 0.0

            # Ensure lists are aligned (zip stops at shortest, but usually they match)
            for name, amount, type_, vat_app, vat_amt in zip(
                ledger_names,
                ledger_amounts,
                ledger_types,
                ledger_vat_applicable,
                ledger_vat_amounts,
            ):
                if type_ == "Debit" and vat_app in ("1", "Yes", "Y", "true", "True"):
                    try:
                        total_input_vat += float(vat_amt or 0)
                    except ValueError:
                        pass

            if total_input_vat > 0:
                ledger_entries.append(
                    {
                        "ledger_name": "Input VAT 5%",
                        "amount": round(total_input_vat, 2),
                        "type": "Debit",
                    }
                )

        # Service Income VAT
        if voucher_type == "Service Income":
            ledger_vat_applicable = request.form.getlist("ledger_vat_applicable[]")
            ledger_vat_amounts = request.form.getlist("ledger_vat_amount[]")
            total_output_vat = 0.0

            for name, amount, type_, vat_app, vat_amt in zip(
                ledger_names,
                ledger_amounts,
                ledger_types,
                ledger_vat_applicable,
                ledger_vat_amounts,
            ):
                if type_ == "Credit" and vat_app in ("1", "Yes", "Y", "true", "True"):
                    try:
                        total_output_vat += float(vat_amt or 0)
                    except ValueError:
                        pass

            if total_output_vat > 0:
                ledger_entries.append(
                    {
                        "ledger_name": "Output VAT 5%",
                        "amount": round(total_output_vat, 2),
                        "type": "Credit",
                    }
                )

        # Service Income Return VAT
        if voucher_type == "Service Income Return":
            ledger_vat_applicable = request.form.getlist("ledger_vat_applicable[]")
            ledger_vat_amounts = request.form.getlist("ledger_vat_amount[]")
            total_output_vat_reversal = 0.0

            for name, amount, type_, vat_app, vat_amt in zip(
                ledger_names,
                ledger_amounts,
                ledger_types,
                ledger_vat_applicable,
                ledger_vat_amounts,
            ):
                if type_ == "Debit" and vat_app in ("1", "Yes", "Y", "true", "True"):
                    try:
                        total_output_vat_reversal += float(vat_amt or 0)
                    except ValueError:
                        pass

            if total_output_vat_reversal > 0:
                ledger_entries.append(
                    {
                        "ledger_name": "Output VAT 5%",
                        "amount": round(total_output_vat_reversal, 2),
                        "type": "Debit",
                    }
                )

            
            
            
        # Balance Validation
        total_debit_check = sum(e['amount'] for e in ledger_entries if e['type'] == 'Debit')
        total_credit_check = sum(e['amount'] for e in ledger_entries if e['type'] == 'Credit')
        
        if abs(total_debit_check - total_credit_check) > 0.05:
             raise ValueError(f"Debit and Credit totals do not match: Debit={total_debit_check}, Credit={total_credit_check}")

        if voucher_type in ["Purchase", "Purchase Return", "Expense"] and not parse_date(request.form.get("original_invoice_date")):
            raise ValueError(f"Invoice Date is required for {voucher_type} vouchers")

        # Call DB Update
        update_voucher_entries(
            voucher_number, 
            date, 
            narration, 
            cost_center_code, 
            ledger_entries,
            credit_days=request.form.get("credit_days"),
            due_date=request.form.get("due_date"),
            original_invoice_ref=request.form.get("original_invoice_ref"),
            original_invoice_date=parse_date(request.form.get("original_invoice_date"))
        )
        
        msg = f"Voucher {voucher_number} updated successfully"
        if is_ajax:
            return jsonify({"success": True, "message": msg})
        flash(msg, "success")
        return redirect(url_for("voucher_bp.voucher", voucher_type=voucher_type))

    except Exception as e:
        print(f"Error updating voucher: {e}")
        if is_ajax:
             return jsonify({"success": False, "message": str(e)}), 400
        flash(str(e), "error")
        # Try to stay on page?
        return redirect(request.referrer or url_for('voucher_bp.voucher', voucher_type='Journal'))


@voucher_bp.route("/get_next_voucher_number")
@login_required
def get_next_voucher_number():
    voucher_type = request.args.get("voucher_type")
    if not voucher_type:
        print("No voucher_type provided for next number")
        return (
            jsonify(
                {"success": False, "message": "Voucher type is required"}
            ),
            400,
        )

    try:
        company_id = get_current_company_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM vouchers WHERE voucher_type = ? AND company_id = ?",
            (voucher_type, company_id),
        )
        count = cursor.fetchone()[0]

        # prefixes
        if voucher_type == "Purchase Return":
            prefix = "PR"
        elif voucher_type == "Sales Return":
            prefix = "SR"
        elif voucher_type == "Stock Adjustment":
            prefix = "SAD"
        elif voucher_type == "Purchase":
            prefix = "PUR"
        elif voucher_type == "Sales":
            prefix = "SAL"
        elif voucher_type == "Service Income":
            prefix = "SRV"
        elif voucher_type == "Inventory Transfer":
            prefix = "ITR"
        elif voucher_type == "Opening":
            prefix = "OPEN"
        else:
            prefix = voucher_type.upper()[:3]

        next_voucher_number = f"{prefix}-{str(count + 1).zfill(5)}"
        conn.close()
        print(
            "Next voucher number for "
            f"{voucher_type}: {next_voucher_number}"
        )
        return jsonify(
            {"success": True, "voucher_number": next_voucher_number}
        )
    except Exception as e:
        print(f"Error getting next voucher number: {str(e)}")
        return (
            jsonify({"success": False, "message": str(e)}),
            500,
        )


@voucher_bp.route("/reverse_voucher")
@login_required
def reverse_voucher_page():
    return render_template("reverse_voucher.html")


@voucher_bp.route("/api/search_vouchers_for_reversal")
@login_required
def api_search_vouchers_for_reversal():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"success": True, "results": []})

    try:
        company_id = get_current_company_id()
        if not company_id:
            return jsonify({"success": False, "message": "No company selected"}), 400
        conn = get_db_connection()
        cursor = conn.cursor()

        allowed_types = ('Receipt', 'Payment', 'Contra', 'Journal', 'Expense')
        placeholders = ','.join('?' for _ in allowed_types)

        sql = f"""
            SELECT v.voucher_number, v.date, v.voucher_type, v.amount, v.narration,
                   (SELECT COUNT(*) FROM vouchers v2 WHERE v2.linked_voucher_number = v.voucher_number AND v2.company_id = v.company_id) as is_reversed
            FROM vouchers v
            WHERE v.voucher_type IN ({placeholders})
            AND v.company_id = ?
            AND (v.voucher_number LIKE ? OR v.narration LIKE ?)
            ORDER BY v.date DESC
            LIMIT 20
        """
        params = list(allowed_types) + [company_id, f"%{query}%", f"%{query}%"]
        
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        
        results = []
        for r in rows:
            results.append({
                "voucher_number": r[0],
                "date": format_date(r[1]),
                "voucher_type": r[2],
                "amount": r[3],
                "narration": r[4] or "",
                "is_reversed": bool(r[5] > 0)
            })
            
        conn.close()
        return jsonify({"success": True, "results": results})
        
    except Exception as e:
        print(f"Error searching vouchers for reversal: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@voucher_bp.route("/api/create_reversal", methods=["POST"])
@login_required
def api_create_reversal():
    try:
        data = request.json
        original_voucher_number = data.get("original_voucher_number")
        reversal_date = parse_date(data.get("reversal_date"))
        narration = data.get("narration")
        
        if not original_voucher_number or not reversal_date:
            return jsonify({"success": False, "message": "Missing required fields"}), 400

        company_id = get_current_company_id()
        if not company_id:
            return jsonify({"success": False, "message": "No company selected"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM vouchers WHERE linked_voucher_number = ? AND company_id = ?", (original_voucher_number, company_id))
        if cursor.fetchone()[0] > 0:
            conn.close()
            return jsonify({"success": False, "message": "This voucher has already been reversed."}), 400

        original_voucher = get_voucher_details(original_voucher_number)
        conn.close()
        
        # Use existing voucher type (Receipt -> Receipt, etc.)
        voucher_type = original_voucher["header"]["voucher_type"]
        original_voucher_number = original_voucher["header"]["voucher_number"]
        
        # Determine strict reversal type map if needed, or just use identical
        # Receipt -> Receipt, Payment -> Payment, Contra -> Contra, Journal -> Journal, Expense -> Expense
        # Note: 'Expense' usually creates a Cr to Party and Dr to Expense. 
        # Reversal should be Dr Party, Cr Expense.
        # This is handled by swapping Ledger Entries types.
        
        ledger_entries = []
        for le in original_voucher.get("ledger_entries", []):
            new_type = "Credit" if le["type"] == "Debit" else "Debit"
            ledger_entries.append({
                "ledger_name": le["ledger_name"],
                "amount": le["amount"],
                "type": new_type,
                "cost_center_code": le.get("cost_center_code")
            })
            
        item_entries = []
        for ie in original_voucher.get("item_entries", []):
             new_type = "Credit" if ie["type"] == "Debit" else "Debit"
             item_entries.append({
                 "item_name": ie["item_name"],
                 "quantity": ie["quantity"],
                 "unit_price": ie["unit_price"],
                 "amount": ie["amount"],
                 "ledger_name": ie.get("ledger_name"),
                 "type": new_type,
                 "cost_center_code": ie.get("cost_center_code"),
                 "location_name": ie.get("location_name")
             })

        final_narration = narration or f"Reversal of Voucher No {original_voucher_number}"
        
        new_voucher_number = add_voucher(
            voucher_type=voucher_type,
            date=reversal_date,
            ledger_entries=ledger_entries,
            item_entries=item_entries,
            narration=final_narration,
            linked_voucher_number=original_voucher_number,
            skip_recalc=False
        )
        
        return jsonify({"success": True, "voucher_number": new_voucher_number})

    except Exception as e:
        print(f"Error creating reversal: {e}")
        return jsonify({"success": False, "message": str(e)}), 500
