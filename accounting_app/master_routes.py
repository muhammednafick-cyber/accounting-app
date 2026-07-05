from flask import Blueprint, render_template, request, jsonify, send_file
import datetime
from flask_login import login_required, current_user
import sqlite3
import io
import xlsxwriter

from database import (
    get_groups,
    get_master_groups, # NEW
    ensure_default_groups,
    get_coa_balances,
    get_inventory_groups,
    get_ledger_details,
    get_inventory_details,
    get_cost_centers,
    get_units,
    add_group,
    delete_group,
    get_sub_groups,
    add_sub_group,
    delete_sub_group,
    add_inventory_group,
    delete_inventory_group,
    add_cost_center,
    delete_cost_center,
    update_cost_center_status,
    get_ledgers,
    add_ledger,
    delete_ledger,
    add_inventory,
    delete_inventory,
    add_unit,
    delete_unit,
    get_company_settings,
    get_locations,
    get_default_location,
    add_voucher,
    update_ledger_credit_terms,
    get_current_company_id,
)
from . import get_db_connection
from .models import parse_date

master_bp = Blueprint("master_bp", __name__)


@master_bp.route("/chart-of-accounts")
@login_required
def chart_of_accounts():
    master_groups = get_master_groups()
    groups = get_groups()
    sub_groups = get_sub_groups()
    ledgers = get_ledger_details()
    
    # Structure: Nature -> Master Group -> Group -> Sub Group -> Ledger
    
    chart = {
        "Assets": {},
        "Liabilities": {},
        "Income": {},
        "Expenses": {}
    }
    
    # Helper to find items
    def get_items_by_parent(items, parent_key, parent_value):
        return [i for i in items if i.get(parent_key) == parent_value]

    # 1. Populate Master Groups
    for nature in chart.keys():
        mgs = [mg for mg in master_groups if mg['nature'] == nature]
        for mg in mgs:
            chart[nature][mg['master_group_name']] = {
                "code": mg['master_group_code'],
                "groups": {}
            }
            
    # 2. Populate Groups
    for g in groups:
        nature = g['nature']
        mg_name = g.get('master_group_name')
        
        if nature in chart:
            target_mg = None
            if mg_name and mg_name in chart[nature]:
                target_mg = chart[nature][mg_name]
            elif "Other" not in chart[nature]:
                 chart[nature]["Other"] = {"code": "", "groups": {}}
                 target_mg = chart[nature]["Other"]
            else:
                 target_mg = chart[nature]["Other"]
            
            if target_mg:
                target_mg["groups"][g['group_name']] = {
                    "code": g['group_code'],
                    "sub_groups": {},
                    "direct_ledgers": []
                }

    # 3. Populate Sub Groups
    for sg in sub_groups:
        # We need to find the group it belongs to
        # sg has group_name and group_code
        g_name = sg['group_name']
        # Find this group in the chart
        found = False
        for nature in chart:
            for mg in chart[nature].values():
                if g_name in mg["groups"]:
                    mg["groups"][g_name]["sub_groups"][sg['sub_group_name']] = {
                        "id": sg['id'],
                        "ledgers": []
                    }
                    found = True
                    break
            if found: break

    # 4. Populate Ledgers
    for l in ledgers:
        g_name = l['group_name']
        sg_name = l.get('sub_group_name')
        
        found_group = None
        for nature in chart:
            for mg in chart[nature].values():
                if g_name in mg["groups"]:
                    found_group = mg["groups"][g_name]
                    break
            if found_group: break
            
        if found_group:
            if sg_name:
                if sg_name in found_group["sub_groups"]:
                    found_group["sub_groups"][sg_name]["ledgers"].append(l)
            else:
                found_group["direct_ledgers"].append(l)

    return render_template("chart_of_accounts.html", chart=chart, username=current_user.username)


@master_bp.route("/api/coa-balances")
@login_required
def api_coa_balances():
    from_date = request.args.get('from_date')
    to_date = request.args.get('to_date')
    balances = get_coa_balances(from_date=from_date, to_date=to_date)
    return jsonify(balances)


# ======================= GROUPS =======================
@master_bp.route("/manage-accounting-master/groups")
@login_required
def manage_groups():
    ensure_default_groups() # Ensure defaults are loaded
    groups = get_groups()
    master_groups = get_master_groups()
    return render_template("groups.html", groups=groups, master_groups=master_groups, username=current_user.username)


@master_bp.route("/add_group", methods=["POST"])
@login_required
def add_new_group():
    group_code = request.form.get("group_code")
    group_name = request.form.get("group_name")
    nature = request.form.get("nature")
    master_group_code = request.form.get("master_group_code")
    try:
        add_group(group_code, group_name, nature, master_group_code)
        return jsonify({"success": True, "message": "Group added successfully!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400


@master_bp.route("/delete_group", methods=["POST"])
@login_required
def delete_existing_group():
    group_name = request.form.get("group_name")
    try:
        delete_group(group_name)
        return jsonify({"success": True, "message": "Group deleted successfully!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400


# ======================= SUB GROUPS (NEW) =======================
@master_bp.route("/manage-accounting-master/sub-groups")
@login_required
def manage_sub_groups():
    groups = get_groups() # For parent selection
    sub_groups = get_sub_groups()
    return render_template("sub_groups.html", groups=groups, sub_groups=sub_groups, username=current_user.username)

@master_bp.route("/add_sub_group", methods=["POST"])
@login_required
def add_new_sub_group():
    sub_group_name = request.form.get("sub_group_name")
    group_code = request.form.get("group_code")
    try:
        if not sub_group_name or not group_code:
            raise Exception("Sub Group Name and Parent Group are required.")
        add_sub_group(sub_group_name, group_code)
        return jsonify({"success": True, "message": "Sub Group added successfully!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400

@master_bp.route("/delete_sub_group", methods=["POST"])
@login_required
def delete_existing_sub_group():
    sub_group_name = request.form.get("sub_group_name")
    try:
        delete_sub_group(sub_group_name)
        return jsonify({"success": True, "message": "Sub Group deleted successfully!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400
        
@master_bp.route("/get_sub_groups_by_group/<group_code>")
@login_required
def get_sub_groups_api(group_code):
    try:
        sub_groups = get_sub_groups(group_code)
        # Fix: access by key since get_sub_groups returns dicts
        data = [{'id': sg['id'], 'name': sg['sub_group_name']} for sg in sub_groups]
        return jsonify({"success": True, "sub_groups": data})
    except Exception as e:
         return jsonify({"success": False, "message": str(e)}), 400


# ======================= LEDGERS =======================
@master_bp.route("/download_sub_group_template")
@login_required
def download_sub_group_template():
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    worksheet = workbook.add_worksheet()

    headers = ["Sub Group Name", "Parent Group Name"]
    for col, header in enumerate(headers):
        worksheet.write(0, col, header)

    # Add sample data
    worksheet.write(1, 0, "Software Assets")
    worksheet.write(1, 1, "Fixed Assets")
    
    workbook.close()
    output.seek(0)

    return send_file(output, download_name="SubGroup_Template.xlsx", as_attachment=True)

    
@master_bp.route("/manage-accounting-master/ledgers")
@login_required
def manage_ledgers():
    groups = get_groups()
    sub_groups = get_sub_groups()
    prefill_ledger_name = request.args.get("name", "")
    return render_template(
        "ledger.html",
        groups=groups,
        sub_groups=sub_groups,
        prefill_ledger_name=prefill_ledger_name,
        username=current_user.username,
    )

@master_bp.route("/api/master/get_ledgers")
@login_required
def get_ledgers_api():
    from database import get_ledgers, get_ledger_details
    try:
        group_code = request.args.get("group_code")
        settlement_filter = request.args.get("settlement_filter")
        auto_post_filter = request.args.get("auto_post_filter")
        voucher_type = request.args.get("voucher_type")
        side = request.args.get("side")  # Debit or Credit
        
        data = []
        
        # Filter by voucher configuration (allowed ledgers for specific voucher type/side)
        if voucher_type and side:
            try:
                from database.voucher_config_db import get_allowed_ledgers, get_voucher_config
                config = get_voucher_config(voucher_type, side)
                if config:
                    allowed = get_allowed_ledgers(voucher_type, side)
                    for l in allowed:
                        data.append({
                            'ledger_code': l.get('code', ''),
                            'ledger_name': l.get('name', ''),
                            'group_code': l.get('group_code', '')
                        })
                    return jsonify(data)
            except Exception as e:
                print(f"Error fetching allowed ledgers: {e}")
            # Fall through to return all ledgers if no config or error
        
        if settlement_filter:
            # Get detailed list to filter by group name aliases (Debtors/Creditors)
            all_ledgers = get_ledger_details()
            target_groups = ['Debtors', 'Creditors', 'Sundry Debtors', 'Sundry Creditors']
            
            for l in all_ledgers:
                # Check nature/group
                g_name = l['group_name']
                if g_name in target_groups:
                    data.append({
                        'ledger_code': l['ledger_code'],
                        'ledger_name': l['ledger_name'],
                        'group_code': l['group_code'],
                        'closing_balance': l['closing_balance']
                    })
        elif auto_post_filter:
            # Filter for Income and Expense groups
            all_ledgers = get_ledger_details()
            target_natures = ['Income', 'Expenses'] 
            # Or strict group names: Direct Expenses, Direct Income, Indirect Expenses, Indirect Income
            target_groups = ['Direct Expenses', 'Direct Income', 'Indirect Expenses', 'Indirect Income']
            
            for l in all_ledgers:
                g_name = l['group_name']
                # We can check exact group names
                if g_name in target_groups:
                    data.append({
                        'ledger_code': l['ledger_code'],
                        'ledger_name': l['ledger_name'],
                        'group_code': l['group_code'],
                        'closing_balance': l['closing_balance']
                    })
        else:
            ledgers = get_ledgers(group_code)
            for l in ledgers:
                # l is dict: code, name, group_code, closing_balance, credit_days
                data.append({
                    'ledger_code': l['ledger_code'],
                    'ledger_name': l['ledger_name'],
                    'group_code': l['group_code'],
                    'closing_balance': l['closing_balance']
                })
                
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@master_bp.route("/add_ledger", methods=["POST"])
@login_required
def add_new_ledger():
    ledger_code = request.form.get("ledger_code")
    ledger_name = request.form.get("ledger_name")
    group_name = request.form.get("group_name")
    sub_group_id = request.form.get("sub_group_id")
    opening_balance = request.form.get("opening_balance") or 0
    opening_balance_type = request.form.get("opening_balance_type", "Debit")

    try:
        groups = get_groups()
        # Fix: access by key
        group_code = next((g['group_code'] for g in groups if g['group_name'] == group_name), None)
        if not group_code:
            return jsonify({"success": False, "message": "Invalid Group Name"}), 400

        add_ledger(
            ledger_code, ledger_name, group_code, opening_balance, opening_balance_type, sub_group_id
        )
        return jsonify({"success": True, "message": "Ledger added successfully!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400


@master_bp.route("/delete_ledger", methods=["POST"])
@login_required
def delete_existing_ledger():
    ledger_name = request.form.get("ledger_name")
    try:
        delete_ledger(ledger_name)
        return jsonify({"success": True, "message": "Ledger deleted successfully!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400


# ======================= COST CENTERS =======================
@master_bp.route("/manage-accounting-master/cost-centers")
@login_required
def manage_cost_centers():
    cost_centers = get_cost_centers()
    return render_template("cost_centers.html", cost_centers=cost_centers, username=current_user.username)


@master_bp.route("/api/master/get_cost_centers")
@login_required
def get_cost_centers_api():
    try:
        # get_cost_centers returns list of tuples or dicts? Checks master_routes imports
        # imports: get_cost_centers
        cc_list = get_cost_centers()
        # Assuming cc_list is list of sqlite3.Row or tuples
        # Based on manage_cost_centers passing it to template, it's iterable.
        # Let's check get_cost_centers implementation or assume row properties
        # If it returns list of tuples: (id, code, name, status, created...)
        # We'll just return list of dicts.
        data = []
        for cc in cc_list:
            # cc is (center_code, center_name, is_active)
            # Index 0: center_code
            # Index 1: center_name
            # Index 2: is_active
            
            # Check is_active
            if cc['is_active']: 
                data.append({
                    'center_code': cc['center_code'],
                    'center_name': cc['center_name']
                })
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@master_bp.route("/add_cost_center", methods=["POST"])
@login_required
def add_new_cost_center():
    center_code = request.form.get("center_code")
    center_name = request.form.get("center_name")
    try:
        add_cost_center(center_code, center_name)
        return jsonify({"success": True, "message": "Cost Center added successfully!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400

@master_bp.route("/update_cost_center_status", methods=["POST"])
@login_required
def update_cc_status():
    center_name = request.form.get("center_name")
    is_active = request.form.get("is_active") == 'true'
    try:
        update_cost_center_status(center_name, is_active)
        return jsonify({"success": True, "message": "Status updated!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400

@master_bp.route("/delete_cost_center", methods=["POST"])
@login_required
def delete_existing_cost_center():
    center_name = request.form.get("center_name")
    try:
        delete_cost_center(center_name)
        return jsonify({"success": True, "message": "Cost Center deleted successfully!"})
    except Exception as e:
         return jsonify({"success": False, "message": str(e)}), 400


# ======================= INVENTORY GROUPS =======================
@master_bp.route("/manage-inventory-master")
@login_required
def manage_inventory_master():
    return render_template("manage_inventory_master.html", username=current_user.username)


@master_bp.route("/manage-inventory-master/inventory_groups")
@login_required
def manage_inventory_groups():
    inventory_groups = get_inventory_groups()
    return render_template("inventory_groups.html", inventory_groups=inventory_groups, username=current_user.username)


@master_bp.route("/add_inventory_group", methods=["POST"])
@login_required
def add_new_inventory_group():
    group_code = request.form.get("group_code")
    group_name = request.form.get("group_name")
    try:
        add_inventory_group(group_code, group_name)
        return jsonify({"success": True, "message": "Inventory Group added successfully!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400


@master_bp.route("/delete_inventory_group", methods=["POST"])
@login_required
def delete_existing_inventory_group():
    group_name = request.form.get("group_name")
    try:
        delete_inventory_group(group_name)
        return jsonify({"success": True, "message": "Inventory Group deleted successfully!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400


# ======================= INVENTORY ITEMS =======================
@master_bp.route("/manage-inventory-master/inventory")
@login_required
def manage_inventory():
    inventory_groups = get_inventory_groups()
    units = get_units()
    company = get_company_settings()
    multiple_locations_enabled = bool(company and company.get('multiple_locations_applicable'))
    locations = get_locations() if multiple_locations_enabled else []
    current_date = datetime.date.today().strftime("%d-%m-%Y")
    all_ledgers = [l['ledger_name'] for l in get_ledgers()]
    return render_template(
        "inventory.html",
        inventory_groups=inventory_groups,
        units=units,
        multiple_locations_enabled=multiple_locations_enabled,
        locations=locations,
        current_date=current_date,
        all_ledgers=all_ledgers,
        company=company,
        username=current_user.username,
    )


# ========= INVENTORY CRUD - FULL VAT SUPPORT =========
@master_bp.route("/add_inventory", methods=["POST"])
@login_required
def add_inventory_route():
    item_code = request.form["item_code"].strip()
    name = request.form["name"].strip()
    stock_group_name = request.form["stock_group_name"]
    unit_code = request.form.get("unit_code", "NOS").strip()
    unit_price = 0.0

    vat_rate = round(float(request.form.get("vat_rate", 5)), 2)

    # Fix: access by key
    group_map = {g['group_name']: g['group_code'] for g in get_inventory_groups()}
    stock_group_code = group_map.get(stock_group_name)

    if not stock_group_code:
        return jsonify({"success": False, "message": "Invalid Group Name"})

    opening_quantity_str = request.form.get("opening_quantity", "")
    opening_price_str = request.form.get("opening_price", "")
    location_name_form = request.form.get("location_name")
    balancing_ledger = request.form.get("balancing_ledger", "").strip()
    purchase_date_str = request.form.get("purchase_date") or request.form.get("fiscal_year")
    try:
        add_inventory(item_code, name, stock_group_code, unit_code, unit_price, vat_rate)
        opening_qty = float(opening_quantity_str) if opening_quantity_str else 0.0
        company = get_company_settings()
        multiple_locations_enabled = bool(company and company.get('multiple_locations_applicable'))
        default_loc = get_default_location()
        final_location_name = None
        if opening_qty > 0:
            if not opening_price_str:
                return jsonify({"success": False, "message": "Opening price (cost) is required when opening quantity is entered"})
            opening_price = round(float(opening_price_str), 2)
            if multiple_locations_enabled and not location_name_form:
                return jsonify({"success": False, "message": "Location is required when opening quantity is entered"})
            if multiple_locations_enabled:
                final_location_name = location_name_form or (default_loc['location_name'] if default_loc else "Main Location")
            else:
                final_location_name = "Main Location"
            conn = get_db_connection()
            cursor = conn.cursor()
            company_id = get_current_company_id()
            stock_val = opening_qty * (opening_price or 0.0)
            cursor.execute(
                "UPDATE inventory SET stock_quantity = %s, opening_price = %s, stock_value = %s WHERE name = %s AND company_id = %s",
                (opening_qty, opening_price, stock_val, name, company_id),
            )
            try:
                # PostgreSQL compatible schema check
                cursor.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'inventory'
                """)
                cols = [row[0] for row in cursor.fetchall()]
                
                if 'opening_location_name' not in cols:
                    cursor.execute("ALTER TABLE inventory ADD COLUMN opening_location_name TEXT")
                if 'opening_price' not in cols:
                    cursor.execute("ALTER TABLE inventory ADD COLUMN opening_price REAL")
            except Exception:
                # Fallback for SQLite
                try:
                    cursor.execute("PRAGMA table_info(inventory)")
                    cols = [row[1] for row in cursor.fetchall()]
                    if 'opening_location_name' not in cols:
                        cursor.execute("ALTER TABLE inventory ADD COLUMN opening_location_name TEXT")
                    if 'opening_price' not in cols:
                        cursor.execute("ALTER TABLE inventory ADD COLUMN opening_price REAL")
                except Exception:
                    pass
            cursor.execute(
                "UPDATE inventory SET opening_location_name = %s WHERE name = %s AND company_id = %s",
                (final_location_name, name, company_id),
            )
            conn.commit()
            conn.close()
            try:
                date_str = parse_date(purchase_date_str) if purchase_date_str else None
                if not date_str:
                    fy = (company or {}).get('financial_year_start') or '01-01'
                    today_year = datetime.date.today().year
                    date_str = f"{today_year}-{fy}"

                item_entries = [{
                    'item_name': name,
                    'quantity': opening_qty,
                    'unit_price': opening_price,
                    'ledger_name': 'Inventory',
                    'type': 'Debit',
                }]
                bal_credit_entries = []
                if balancing_ledger:
                    bal_credit_entries = [{'ledger_name': balancing_ledger, 'amount': round(opening_qty * opening_price, 2), 'type': 'Credit'}]
                add_voucher(
                    'Opening',
                    date_str,
                    ledger_entries=bal_credit_entries,
                    item_entries=item_entries,
                    narration='Auto Opening from Manage Inventory',
                    location_name=final_location_name,
                )
            except Exception as e:
                print('Auto Opening voucher creation failed:', str(e))
                # Re-raise to notify user
                return jsonify({"success": False, "message": f"Item added but Opening Voucher failed: {str(e)}"})
        return jsonify({"success": True, "message": "Item added successfully!"})
    except Exception as e:
        print(f"Error adding inventory: {str(e)}")
        return jsonify({"success": False, "message": str(e)})


@master_bp.route("/delete_inventory", methods=["POST"])
@login_required
def delete_inventory_route():
    item_name = request.form["item_name"]
    try:
        delete_inventory(item_name)
        return jsonify({"success": True, "message": f"Item {item_name} deleted!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ========= Export Existing Ledgers =========
@master_bp.route("/export_ledger")
@login_required
def export_ledger():
    try:
        ledgers = get_ledger_details()
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet("Ledgers")

        headers = [
            "Ledger Code", "Ledger Name", "Group Code", "Group Name",
            "Nature", "Opening Balance", "Opening Type", "Closing Balance"
        ]
        for col, header in enumerate(headers):
            worksheet.write(0, col, header)

        for row, ledger in enumerate(ledgers, start=1):
            # ledger is dict
            worksheet.write(row, 0, ledger.get('ledger_code', ''))
            worksheet.write(row, 1, ledger.get('ledger_name', ''))
            worksheet.write(row, 2, ledger.get('group_code', ''))
            worksheet.write(row, 3, ledger.get('group_name', ''))
            worksheet.write(row, 4, ledger.get('nature', ''))
            worksheet.write(row, 5, ledger.get('opening_balance', 0))
            worksheet.write(row, 6, ledger.get('opening_balance_type', ''))
            worksheet.write(row, 7, ledger.get('closing_balance', 0))

        workbook.close()
        output.seek(0)
        return send_file(
            output,
            download_name="existing_ledgers.xlsx",
            as_attachment=True,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        print(f"Error exporting ledger: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500


# ======================= CREDIT TERMS =======================
@master_bp.route("/manage-accounting-master/credit-terms")
@login_required
def manage_credit_terms():
    all_ledgers = get_ledger_details()
    # ledgers rows: 0:code, 1:name, 2:g_code, 3:g_name, 4:nature, 5:op_bal, 6:op_type, 7:cl_bal, 8:sub_group, 9:credit_days
    
    filtered_ledgers = []
    for l in all_ledgers:
        try:
            # Fix: access by key
            # ledgers returns specific columns, need to match get_ledger_details
            ledger_code = l.get('ledger_code')
            ledger_name = l.get('ledger_name')
            group_name = l.get('group_name')
            credit_days = l.get('credit_days', 0)
            
            # Normalize group name for checking
            g_check = str(group_name).strip()
            
            # Check if group is Debtors or Creditors (or aliases)
            # User specifically mentioned "Debtors" and "Creditors" groups.
            if g_check in ['Debtors', 'Creditors', 'Sundry Debtors', 'Sundry Creditors']:
                filtered_ledgers.append({
                    'ledger_code': ledger_code,
                    'ledger_name': ledger_name,
                    'group_name': group_name,
                    'credit_days': credit_days
                })
        except Exception as e:
            print(f"Error processing ledger row {l}: {e}")
            pass
            
    return render_template("manage_credit_terms.html", ledgers=filtered_ledgers, username=current_user.username)

@master_bp.route("/update_credit_terms", methods=["POST"])
@login_required
def update_credit_terms():
    data = request.get_json()
    ledger_code = data.get("ledger_code")
    credit_days = data.get("credit_days")
    try:
        update_ledger_credit_terms(ledger_code, credit_days)
        return jsonify({"success": True, "message": "Credit terms updated!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400

@master_bp.route("/import_credit_terms", methods=["POST"])
@login_required
def import_credit_terms():
    try:
        data = request.get_json()
        rows = data.get("data", [])
        
        # Build map of Name -> Code for quick lookup
        all_ledgers = get_ledger_details()
        name_map = {l['ledger_name'].lower(): l['ledger_code'] for l in all_ledgers}
        
        success_count = 0
        errors = []
        
        for row in rows:
            # Flexible key access
            name = row.get("Ledger Name") or row.get("Party Name") or row.get("Name")
            days = row.get("Credit Days") or row.get("Days") or row.get("Credit Terms")
            
            if name and days is not None:
                code = name_map.get(str(name).strip().lower())
                if code:
                    try:
                        update_ledger_credit_terms(code, int(days))
                        success_count += 1
                    except Exception:
                        errors.append(f"Failed to update {name}")
                else:
                    errors.append(f"Ledger not found: {name}")
        
        msg = f"Updated {success_count} ledgers."
        if errors:
            msg += f" Errors: {len(errors)}"
            
        return jsonify({"success": True, "message": msg})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400


# ======================= VENDOR ITEM MAPPINGS =======================
@master_bp.route("/manage-inventory-master/vendor-item-mappings")
@login_required
def vendor_item_mappings():
    from database.item_mapping_db import get_all_mappings, get_mappings_by_vendor
    from database.inventory_db import get_inventory_details
    
    selected_vendor = request.args.get('vendor', '')
    
    # Get mappings
    if selected_vendor:
        mappings = get_mappings_by_vendor(selected_vendor)
    else:
        mappings = get_all_mappings()
    
    # Get unique vendors from existing mappings
    all_mappings = get_all_mappings()
    vendors = sorted(set(m['vendor'] for m in all_mappings))
    
    # Get inventory items for datalist
    items = get_inventory_details()
    items_list = [{'item_code': i['item_code'], 'item_name': i['name']} for i in items] if items else []
    
    return render_template(
        "vendor_item_mappings.html",
        mappings=mappings,
        vendors=vendors,
        items=items_list,
        selected_vendor=selected_vendor,
        username=current_user.username
    )

@master_bp.route("/add_vendor_mapping", methods=["POST"])
@login_required
def add_vendor_mapping():
    from database.item_mapping_db import add_item_mapping
    
    vendor_name = request.form.get("vendor_name", "").strip()
    vendor_item_name = request.form.get("vendor_item_name", "").strip()
    app_item_code = request.form.get("app_item_code", "").strip()
    
    if not vendor_name or not vendor_item_name or not app_item_code:
        return jsonify({"success": False, "message": "All fields are required"}), 400
    
    try:
        add_item_mapping(vendor_name, vendor_item_name, app_item_code)
        return jsonify({"success": True, "message": "Mapping added successfully!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400

@master_bp.route("/delete_vendor_mapping", methods=["POST"])
@login_required
def delete_vendor_mapping():
    from database.item_mapping_db import delete_item_mapping
    
    vendor_name = request.form.get("vendor_name", "").strip()
    vendor_item_name = request.form.get("vendor_item_name", "").strip()
    
    try:
        if delete_item_mapping(vendor_name, vendor_item_name):
            return jsonify({"success": True, "message": "Mapping deleted!"})
        else:
            return jsonify({"success": False, "message": "Mapping not found"}), 404
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400


@master_bp.route("/download_vendor_mapping_template")
@login_required
def download_vendor_mapping_template():
    """Download Excel template for vendor item mappings."""
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    worksheet = workbook.add_worksheet("Vendor Item Mappings")
    
    # Headers
    headers = ["Vendor Name", "Vendor Item Name", "App Item Name"]
    header_format = workbook.add_format({'bold': True, 'bg_color': '#4472C4', 'font_color': 'white'})
    
    for col, header in enumerate(headers):
        worksheet.write(0, col, header, header_format)
        worksheet.set_column(col, col, 25)
    
    # Sample row
    worksheet.write(1, 0, "ABC Suppliers")
    worksheet.write(1, 1, "ITEM-001-VENDOR")
    worksheet.write(1, 2, "My Item Name")
    
    workbook.close()
    output.seek(0)
    
    return send_file(
        output,
        download_name="vendor_item_mappings_template.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@master_bp.route("/import_vendor_mappings", methods=["POST"])
@login_required
def import_vendor_mappings():
    """Import vendor item mappings from Excel."""
    from database.item_mapping_db import add_item_mapping
    
    try:
        data = request.get_json()
        rows = data.get("data", [])
        
        if not rows:
            return jsonify({"success": False, "message": "No data to import"}), 400
        
        success_count = 0
        errors = []
        
        for idx, row in enumerate(rows, start=2):
            # Flexible column access
            vendor_name = row.get("Vendor Name") or row.get("vendor_name") or ""
            vendor_item = row.get("Vendor Item Name") or row.get("vendor_item_name") or row.get("Vendor Item") or ""
            app_item = row.get("App Item Name") or row.get("app_item_name") or row.get("App Item") or ""
            
            vendor_name = str(vendor_name).strip()
            vendor_item = str(vendor_item).strip()
            app_item = str(app_item).strip()
            
            if not vendor_name or not vendor_item or not app_item:
                errors.append(f"Row {idx}: Missing required field(s)")
                continue
            
            try:
                add_item_mapping(vendor_name, vendor_item, app_item)
                success_count += 1
            except Exception as e:
                errors.append(f"Row {idx}: {str(e)}")
        
        msg = f"Imported {success_count} mappings."
        if errors:
            msg += f" Errors: {len(errors)} (first: {errors[0]})"
        
        return jsonify({"success": True, "message": msg, "errors": errors[:5]})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400


