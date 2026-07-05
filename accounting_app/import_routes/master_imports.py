import json
import sqlite3
from datetime import datetime

from flask import jsonify, request
from flask_login import login_required

from . import import_bp
from .utils import validate_import_data, insert_import_queue
from accounting_app import get_db_connection
from accounting_app.models import parse_date
from database import add_inventory, get_inventory_groups, get_company_settings, get_default_location, add_voucher, get_current_company_id, get_groups, get_sub_groups


@import_bp.route("/queue_group_import", methods=["POST"])
@login_required
def queue_group_import():
    data = request.json
    original_file_name = data["file_name"]
    json_data = data["json_data"]
    date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    file_name = f"Group_{date}.json"

    print(
        "Received group import: "
        f"original_file_name={original_file_name}, json_data={json_data}"
    )

    failure_reason = None
    try:
        groups_data = json.loads(json_data)
        is_valid, vmsg = validate_import_data("Group", groups_data)
        validation_status = "Success" if is_valid else "Failed"
        if not is_valid:
            failure_reason = vmsg
        print(f"Group validation: {validation_status} - {failure_reason}")
    except json.JSONDecodeError as e:
        print(f"Group JSON decode error: {e}")
        validation_status = "Failed"
        failure_reason = f"Invalid JSON data: {str(e)}"

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        company_id = get_current_company_id()

        new_id = insert_import_queue(
            cursor, company_id, date, file_name, "Group", json_data,
            validation_status, "In Progress", failure_reason=failure_reason
        )
        
        conn.commit()
        print(
            "Inserted group import: "
            f"id={new_id}, date={date}, file_name={file_name}, "
            f"validation_status={validation_status}"
        )
        conn.close()
        return jsonify(
            {
                "success": True,
                "message": "Group import queued! Check Import Queue report",
            }
        )
    except Exception as e:
        print(f"Database error queuing group import: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify(
            {
                "success": False,
                "message": f"Error queuing group import: {str(e)}",
            }
        )

# ======================= NEW: SUB GROUP IMPORT =======================
@import_bp.route("/queue_sub_group_import", methods=["POST"])
@login_required
def queue_sub_group_import():
    data = request.json
    original_file_name = data["file_name"]
    json_data = data["json_data"]
    date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    file_name = f"SubGroup_{date}.json"

    print(f"Received sub group import: original_file_name={original_file_name}")

    try:
        sub_groups_data = json.loads(json_data)
        
        # Validation: Check presence of fields AND existence of Parent Group in DB
        validation_status = "Success"
        failure_reasons = []
        
        # Fetch existing groups for validation
        # We need company_id here. It is fetched later in original code, need it now.
        company_id = get_current_company_id()
        if not company_id:
             validation_status = "Failed"
             failure_reasons.append("No company selected.")
        else:
            all_groups = get_groups(company_id=company_id)
            all_sub_groups = get_sub_groups(company_id=company_id)
            
            # Create sets for fast lookup
            valid_group_names = { (g['group_name'] or "").strip().lower() for g in all_groups }
            # sub_groups usually (group_code, sub_group_name, parent_group_code, id)
            # We need to check if sub_group_name exists
            existing_sub_group_names = { (sg['sub_group_name'] or "").strip().lower() for sg in all_sub_groups }

            for i, row in enumerate(sub_groups_data):
                sub_name = row.get('Sub Group Name')
                parent_name = row.get('Parent Group Name') or row.get('Parent Group')
                
                if not sub_name:
                    validation_status = "Failed"
                    failure_reasons.append(f"Row {i+1}: Missing Sub Group Name")
                    continue
                    
                if not parent_name:
                    validation_status = "Failed"
                    failure_reasons.append(f"Row {i+1}: Missing Parent Group for '{sub_name}'")
                    continue
                
                if parent_name.strip().lower() not in valid_group_names:
                    validation_status = "Failed"
                    failure_reasons.append(f"Row {i+1}: Parent Group '{parent_name}' does not exist.")
                    continue
                
                if sub_name.strip().lower() in existing_sub_group_names:
                    validation_status = "Failed"
                    failure_reasons.append(f"Row {i+1}: Sub Group '{sub_name}' already exists.")
                    continue
        
        if failure_reasons:
            print(f"Sub Group validation failed: {failure_reasons}")
            # We can optionally store these reasons in the failure_reason column if we join them
        
        print(f"Sub Group validation: {validation_status}")
    except json.JSONDecodeError as e:
        print(f"Sub Group JSON decode error: {e}")
        validation_status = "Failed"
        failure_reasons = [f"JSON Error: {str(e)}"]
    except Exception as e:
        print(f"Sub Group validation error: {str(e)}")
        import traceback
        traceback.print_exc()
        validation_status = "Failed"
        failure_reasons = [f"Validation Error: {str(e)}"]

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        company_id = get_current_company_id()
        
        conn = get_db_connection()
        cursor = conn.cursor()
        company_id = get_current_company_id()
        
        final_failure_reason = "; ".join(failure_reasons) if failure_reasons else None
        
        new_id = insert_import_queue(
            cursor, company_id, date, file_name, "Sub Group", json_data, 
            validation_status, "In Progress", failure_reason=final_failure_reason
        )
        
        conn.commit()
        print(f"Inserted sub group import id={new_id}")
        conn.close()
        return jsonify(
            {"success": True, "message": "Sub Group import queued! Check Import Queue report"}
        )
    except Exception as e:
        print(f"Database error queuing sub group import: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify(
            {"success": False, "message": f"Error queuing sub group import: {str(e)}"}
        )

# ======================= NEW: LEDGER IMPORT =======================
@import_bp.route("/queue_ledger_import", methods=["POST"])
@login_required
def queue_ledger_import():
    data = request.json
    original_file_name = data["file_name"]
    json_data = data["json_data"]
    date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    file_name = f"Ledger_{date}.json"

    print(f"Received ledger import: original_file_name={original_file_name}")

    failure_reason = None
    try:
        ledger_data = json.loads(json_data)
        if not isinstance(ledger_data, list):
            ledger_data = [ledger_data]
        is_valid, vmsg = validate_import_data("Ledger", ledger_data)
        if not is_valid:
            failure_reason = vmsg
        else:
            # Check parent Groups exist so upload from the queue always succeeds
            company_id_chk = get_current_company_id()
            groups = {(g['group_name'] or "").strip().lower() for g in get_groups(company_id=company_id_chk)}
            missing_groups = set()
            for row in ledger_data:
                gname = str(row.get("group_name") or "").strip()
                if gname and gname.lower() not in groups:
                    missing_groups.add(gname)
            if missing_groups:
                is_valid = False
                failure_reason = "Missing Groups: " + ", ".join(sorted(missing_groups))
        validation_status = "Success" if is_valid else "Failed"
        print(f"Ledger validation: {validation_status} - {failure_reason}")
    except json.JSONDecodeError as e:
        print(f"Ledger JSON decode error: {e}")
        validation_status = "Failed"
        failure_reason = f"Invalid JSON data: {str(e)}"

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        company_id = get_current_company_id()

        new_id = insert_import_queue(
            cursor, company_id, date, file_name, "Ledger", json_data,
            validation_status, "In Progress", failure_reason=failure_reason
        )
        conn.commit()
        print(f"Inserted ledger import id={new_id}")
        conn.close()
        return jsonify(
            {"success": True, "message": "Ledger import queued! Check Import Queue report"}
        )
    except Exception as e:
        print(f"Database error queuing ledger import: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify(
            {"success": False, "message": f"Error queuing ledger import: {str(e)}"}
        )


# ======================= EXISTING ROUTES (PRESERVED) =======================
# (All your original routes below — nothing removed)

@import_bp.route("/queue_cost_center_import", methods=["POST"])
@login_required
def queue_cost_center_import():
    data = request.json
    original_file_name = data["file_name"]
    json_data = data["json_data"]
    date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    file_name = f"CostCenter_{date}.json"

    print(
        "Received cost center import: "
        f"original_file_name={original_file_name}, json_data={json_data}"
    )

    failure_reason = None
    try:
        cost_center_data = json.loads(json_data)
        is_valid, vmsg = validate_import_data("Cost Center", cost_center_data)
        validation_status = "Success" if is_valid else "Failed"
        if not is_valid:
            failure_reason = vmsg
        print(f"Cost Center validation: {validation_status} - {failure_reason}")
    except json.JSONDecodeError as e:
        print(f"Cost Center JSON decode error: {e}")
        validation_status = "Failed"
        failure_reason = f"Invalid JSON data: {str(e)}"

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        company_id = get_current_company_id()

        new_id = insert_import_queue(
            cursor, company_id, date, file_name, "Cost Center", json_data,
            validation_status, "In Progress", failure_reason=failure_reason
        )
        conn.commit()
        print(
            "Inserted cost center import: "
            f"id={new_id}, date={date}, file_name={file_name}, "
            f"validation_status={validation_status}"
        )
        conn.close()
        return jsonify(
            {
                "success": True,
                "message": "Cost Center import queued! Check Import Queue report",
            }
        )
    except Exception as e:
        print(f"Database error queuing cost center import: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify(
            {
                "success": False,
                "message": f"Error queuing cost center import: {str(e)}",
            }
        )


@import_bp.route("/queue_inventory_group_import", methods=["POST"])
@login_required
def queue_inventory_group_import():
    data = request.json
    original_file_name = data["file_name"]
    json_data = data["json_data"]
    date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    file_name = f"InventoryGroup_{date}.json"

    print(
        "Received inventory group import: "
        f"original_file_name={original_file_name}, json_data={json_data}"
    )

    failure_reason = None
    try:
        inv_group_data = json.loads(json_data)
        is_valid, vmsg = validate_import_data("Inventory Group", inv_group_data)
        validation_status = "Success" if is_valid else "Failed"
        if not is_valid:
            failure_reason = vmsg
        print(f"Inventory Group validation: {validation_status} - {failure_reason}")
    except json.JSONDecodeError as e:
        print(f"Inventory Group JSON decode error: {e}")
        validation_status = "Failed"
        failure_reason = f"Invalid JSON data: {str(e)}"

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        company_id = get_current_company_id()

        new_id = insert_import_queue(
            cursor, company_id, date, file_name, "Inventory Group", json_data,
            validation_status, "In Progress", failure_reason=failure_reason
        )
        conn.commit()
        print(
            "Inserted inventory group import: "
            f"id={new_id}, date={date}, file_name={file_name}, "
            f"validation_status={validation_status}"
        )
        conn.close()
        return jsonify(
            {
                "success": True,
                "message": "Inventory Group import queued! Check Import Queue report",
            }
        )
    except Exception as e:
        print(f"Database error queuing inventory group import: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify(
            {
                "success": False,
                "message": f"Error queuing inventory group import: {str(e)}",
            }
        )


# ======================= INVENTORY IMPORT =======================
@import_bp.route("/queue_inventory_import", methods=["POST"])
@login_required
def queue_inventory_import():
    data = request.json
    original_file_name = data["file_name"]
    json_data = data["json_data"]
    fiscal_year = data.get("fiscal_year")
    date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    file_name = f"Inventory_{date}.json"

    print(
        "Received inventory import: "
        f"original_file_name={original_file_name}, json_data={json_data}, fiscal_year={fiscal_year}"
    )

    validation_error_msg = None
    missing_ledger_str = None
    inventory_data = []
    company_id = get_current_company_id()
    try:
        inventory_data = json.loads(json_data)
        if not isinstance(inventory_data, list):
            inventory_data = [inventory_data]
        valid_result = validate_import_data("Inventory", inventory_data)
        # validate_import_data returns (bool, msg) tuple
        is_valid = valid_result[0] if isinstance(valid_result, tuple) else bool(valid_result)
        if not is_valid:
            validation_error_msg = valid_result[1] if isinstance(valid_result, tuple) else "Validation failed"
        validation_status = "Success" if is_valid else "Failed"
        print(f"Inventory validation: {validation_status}")
    except json.JSONDecodeError as e:
        print(f"Inventory JSON decode error: {e}")
        validation_status = "Failed"
        validation_error_msg = f"Invalid JSON data: {str(e)}"

    # ---- DB-LEVEL VALIDATION (so upload from the Import Queue always succeeds) ----
    # Checks: Inventory Group exists, Balancing Ledger exists, Purchase Date is
    # parseable and covered by a defined Financial Year.
    if validation_status == "Success":
        errors = []
        missing_ledgers = set()
        try:
            from database import get_ledgers
            from database.financial_year_db import get_fy_by_date

            inv_groups = {(g['group_name'] or "").strip().lower() for g in get_inventory_groups(company_id=company_id)}
            ledger_names = {row['ledger_name'] for row in get_ledgers(company_id=company_id)}

            missing_inv_groups = set()
            bad_dates = []
            missing_fy_dates = set()

            for row in inventory_data:
                gname = str(row.get("Group Name") or "").strip()
                if gname and gname.lower() not in inv_groups:
                    missing_inv_groups.add(gname)

                try:
                    oq = float(row.get("Opening Quantity") or 0)
                except (ValueError, TypeError):
                    oq = 0.0
                if oq > 0:
                    bl = str(row.get("Balancing Ledger") or "").strip()
                    if bl and bl not in ledger_names:
                        missing_ledgers.add(bl)

                # Any provided Purchase Date must be valid and inside a defined
                # Financial Year, even for rows with zero Opening Quantity.
                pd_raw = str(row.get("Purchase Date") or "").strip()
                if pd_raw or oq > 0:
                    pd_norm = parse_date(pd_raw) if pd_raw else None
                    # parse_date returns the original string on failure, so
                    # verify the result really is YYYY-MM-DD
                    try:
                        datetime.strptime(str(pd_norm), "%Y-%m-%d")
                    except (ValueError, TypeError):
                        pd_norm = None
                    if not pd_norm:
                        bad_dates.append(f"'{pd_raw}' (item '{row.get('Item Name')}')")
                    elif not get_fy_by_date(pd_norm, company_id=company_id):
                        missing_fy_dates.add(pd_norm)

            if missing_inv_groups:
                errors.append("Missing Inventory Groups: " + ", ".join(sorted(missing_inv_groups)))
            if missing_ledgers:
                errors.append("Missing Balancing Ledgers: " + ", ".join(sorted(missing_ledgers)))
            if bad_dates:
                errors.append("Invalid Purchase Dates: " + "; ".join(bad_dates[:5]))
            if missing_fy_dates:
                errors.append("No Financial Year defined for Purchase Date(s): " + ", ".join(sorted(missing_fy_dates)) + ". Create the Financial Year(s) first (Admin > Manage Financial Years).")
        except Exception as e:
            print(f"Inventory DB validation error: {e}")
            errors.append(f"Validation check error: {str(e)}")

        if errors:
            validation_status = "Failed"
            validation_error_msg = "; ".join(errors)
        if missing_ledgers:
            missing_ledger_str = ",".join(sorted(missing_ledgers))

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        new_id = insert_import_queue(
            cursor, company_id, date, file_name, "Inventory", json_data,
            validation_status, "In Progress",
            missing_ledger=missing_ledger_str,
            failure_reason=validation_error_msg
        )
        conn.commit()
        print(
            "Inserted inventory import: "
            f"id={new_id}, date={date}, file_name={file_name}, "
            f"validation_status={validation_status}"
        )
        
        # Processing happens only via upload_import from the Import Queue page.
        # Processing here as well created duplicate items and Opening vouchers.
        conn.close()
        if validation_status == "Failed":
            return jsonify({
                "success": False,
                "message": f"Validation failed: {validation_error_msg} — the entry has been added to the Import Queue with details. Fix the issues and upload the file again.",
            }), 400
        return jsonify({
            "success": True,
            "message": "Inventory import validated and queued. Open the Import Queue to process it.",
        })

        # Legacy inline processing (disabled — kept for reference)
        try:
            inv_list = json.loads(json_data)
            if not isinstance(inv_list, list):
                inv_list = [inv_list]
                
            # FIX: Use key access for DictRow
            raw_groups = get_inventory_groups()
            group_map = {g['group_name']: g['group_code'] for g in raw_groups}
            
            company = get_company_settings()
            multiple_locations_enabled = bool(company and company.get('multiple_locations_applicable'))
            default_loc = get_default_location()
            date_str = parse_date(fiscal_year) if fiscal_year else None
            if not date_str:
                # Use earliest defined FY start as the fallback date
                from database.financial_year_db import get_all_fys
                all_fys = get_all_fys() or []
                if all_fys:
                    date_str = min(fy['start_date'] for fy in all_fys)
                else:
                    today_year = datetime.now().year
                    fy_start = (company or {}).get('financial_year_start') or '01-01'
                    date_str = f"{today_year}-{fy_start}"

            for entry in inv_list:
                item_code = entry.get("Item Code")
                name = entry.get("Item Name")
                # Strip spaces if name is mistakenly padded (common import issue)
                if name: name = name.strip()
                
                group_name = entry.get("Group Name")
                unit_code = entry.get("Unit")
                unit_price = 0.0
                vat_rate = entry.get("VAT 5%", 0)

                try:
                    vat_rate = round(float(vat_rate or 0), 2)
                except Exception:
                    vat_rate = 0.0
                stock_group_code = group_map.get(group_name)
                
                if not all([item_code, name, stock_group_code, unit_code]):
                    continue
                
                # Mimic v7 logic: Separate transactions for each step
                try:
                    # 1. Add/Update Inventory Master
                    add_inventory(item_code, name, stock_group_code, unit_code, unit_price, vat_rate, company_id=company_id)
                    
                    opening_qty = entry.get("Opening Quantity")
                    opening_price = entry.get("Opening Price (Cost)")
                    try:
                        opening_qty = float(opening_qty) if opening_qty not in (None, "") else 0.0
                    except Exception:
                        opening_qty = 0.0
                    try:
                        opening_price = round(float(opening_price), 2) if opening_price not in (None, "") else 0.0
                    except Exception:
                        opening_price = 0.0
                    
                    if opening_qty > 0 and opening_price > 0:
                        # Mandatory fields when opening quantity is provided
                        purchase_date_val = str(entry.get("Purchase Date", "") or "").strip()
                        balancing_ledger_val = str(entry.get("Balancing Ledger", "") or "").strip()
                        if not purchase_date_val:
                            raise Exception(f"Purchase Date is required for item '{name}' when Opening Quantity > 0")
                        if not balancing_ledger_val:
                            raise Exception(f"Balancing Ledger is required for item '{name}' when Opening Quantity > 0")

                        final_location_name = None
                        if multiple_locations_enabled:
                            final_location_name = entry.get("Location Name") or (default_loc[2] if default_loc else "Main Location")
                        else:
                            final_location_name = "Main Location"

                        # Calculate initial stock value
                        initial_stock_value = round(opening_qty * opening_price, 2)

                        # Determine opening date — prefer Purchase Date, fall back to Opening Date, then default
                        opening_date_input = entry.get("Purchase Date") or entry.get("Opening Date")
                        final_date_str = parse_date(opening_date_input) if opening_date_input else date_str
                        if not final_date_str:
                            final_date_str = date_str

                        balancing_ledger = str(entry.get("Balancing Ledger", "") or "").strip()

                        # 2. Update Inventory Master with Opening Info (Separate Connection)
                        c2 = get_db_connection()
                        try:
                            cur2 = c2.cursor()
                            cur2.execute("UPDATE inventory SET stock_quantity = %s, opening_price = %s, stock_value = %s, opening_location_name = %s WHERE name = %s AND company_id = %s", (opening_qty, opening_price, initial_stock_value, final_location_name, name, company_id))
                            c2.commit()
                        finally:
                            c2.close()

                        item_entries = [{
                            'item_name': name,
                            'quantity': opening_qty,
                            'unit_price': opening_price,
                            'ledger_name': 'Inventory',
                            'type': 'Debit',
                        }]
                        bal_credit_entries = []
                        if balancing_ledger:
                            bal_credit_entries = [{'ledger_name': balancing_ledger, 'amount': initial_stock_value, 'type': 'Credit'}]

                        # 3. Add Opening Voucher (Separate Connection managed by add_voucher)
                        add_voucher(
                            voucher_type='Opening',
                            date=final_date_str,
                            ledger_entries=bal_credit_entries,
                            item_entries=item_entries,
                            narration='Auto Opening from Import',
                            location_name=final_location_name,
                            company_id=company_id
                        )
                except Exception as e:
                    print(f"Error processing item {name}: {str(e)}")
                    # Continue to next item even if this one fails? v7 doesn't seem to catch here, but global try/catch catches it.
                    # If we want to process as many as possible, we catch here.
                    # But v7 loop structure in master_imports.py (which I read) was:
                    # for entry in entries: ... logic ...
                    # It was inside a `try...except` block for the whole route?
                    # The v9 code I'm editing is inside `for entry in entries:`.
                    # I'll re-raise to be safe or just print. 
                    # If I raise, the whole import stops.
                    # Let's raise to see errors.
                    raise e

        except Exception as e:
            print(f"Global Import Error: {str(e)}")
            
        conn.close()
        return jsonify(
            {
                "success": True,
                "message": "Inventory imported and queued",
            }
        )
    except Exception as e:
        print(f"Database error queuing inventory import: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify(
            {
                "success": False,
                "message": f"Error queuing inventory import: {str(e)}",
            }
        )
