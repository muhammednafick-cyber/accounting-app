import io
import json
import sqlite3
from datetime import datetime
import xlsxwriter
from flask import jsonify, render_template, request, send_file
from flask_login import current_user, login_required
from . import import_bp
from .utils import validate_import_data
from accounting_app import get_db_connection
from accounting_app.models import format_date, get_cost_center_code, parse_date
from database import (
    add_group,
    add_ledger,
    add_cost_center,
    add_inventory_group,
    add_inventory,
    get_groups,
    get_inventory_groups,
    get_ledgers,
    get_items,
    get_locations,
    get_company_settings,
    get_default_location,
    add_voucher,
    get_current_stock, # Import stock check function
    get_cost_centers,
    get_current_company_id,
)


@import_bp.route("/queue_import", methods=["POST"])
@login_required
def queue_import():
    company_id = get_current_company_id()
    try:
        if not request.is_json:
            print("Request is not JSON")
            return jsonify(
                success=False,
                message="Invalid request: Content-Type must be application/json",
            ), 400

        data = request.get_json()
        original_file_name = data.get("file_name")
        voucher_type = data.get("voucher_type")
        json_data = data.get("json_data")

        if not all([original_file_name, voucher_type, json_data]):
            print(
                "Missing fields: "
                f"file_name={original_file_name}, voucher_type={voucher_type}, json_data={json_data}"
            )
            return jsonify(
                success=False,
                message="Missing required fields: file_name, voucher_type, json_data",
            ), 400

        print(
            "Received: "
            f"original_file_name={original_file_name}, "
            f"voucher_type={voucher_type}, json_data={json_data}"
        )

        try:
            import_data = json.loads(json_data)
        except json.JSONDecodeError as e:
            print(f"JSON decode error: {e}")
            return jsonify(
                success=False,
                message=f"Invalid JSON data: {str(e)}",
            ), 400

        validation_status = "Not Applicable"
        validation_msg = None

        # Master imports (Group/Ledger/etc.) -> list
        if voucher_type in [
            "Group",
            "Ledger",
            "Cost Center",
            "Inventory Group",
            "Sub Group",
            "Inventory",
        ]:
            if not isinstance(import_data, list):
                import_data = [import_data]

            is_valid, msg = validate_import_data(voucher_type, import_data, company_id=company_id)
            
            # --- Master Data DB Validation ---
            if is_valid:
                if voucher_type == "Ledger":
                     # Check if Groups exist
                     from database import get_groups
                     groups = { (g[1] or "").strip().lower() for g in get_groups(company_id=company_id) }
                     missing_groups = set()
                     for row in import_data:
                         gname = (row.get("group_name") or "").strip().lower()
                         if gname and gname not in groups:
                             missing_groups.add(row.get("group_name"))
                     if missing_groups:
                         is_valid = False
                         msg = f"Missing Groups: {', '.join(sorted(missing_groups))}"

                elif voucher_type == "Inventory":
                     # Check if Inventory Groups exist
                     from database import get_inventory_groups
                     inv_groups = { (g['group_name'] or "").strip().lower() for g in get_inventory_groups(company_id=company_id) }
                     missing_inv_groups = set()
                     for row in import_data:
                         gname = (row.get("Group Name") or "").strip().lower()
                         if gname and gname not in inv_groups:
                             missing_inv_groups.add(row.get("Group Name"))
                     if missing_inv_groups:
                         is_valid = False
                         msg = f"Missing Inventory Groups: {', '.join(sorted(missing_inv_groups))}"

            validation_status = "Success" if is_valid else "Failed"
            if not is_valid:
                validation_msg = msg
            
            print(f"Validation for {voucher_type}: {validation_status} - {validation_msg}")
        else:
            # Voucher imports
            validation_status = None
            # If import_data is a list, it means we have raw rows from Excel that need grouping
            if isinstance(import_data, list):
                try:
                    import_data = _group_voucher_rows(voucher_type, import_data, company_id=company_id)
                    
                    # Sort vouchers by date to ensure chronological processing
                    # This is critical for stock validation logic
                    import_data.sort(key=lambda x: x.get("date", ""))
                    
                    # Update json_data to reflect the grouped structure
                    json_data = json.dumps(import_data)

                except Exception as e:
                    print(f"Error grouping voucher rows: {e}")
                    validation_status = "Failed"
                    validation_msg = f"Error grouping voucher rows: {str(e)}"
                    import_data = [] # Empty data to proceed to insert
            
            # Ensure it is a list of vouchers now (even if single voucher, wrap in list)
            if isinstance(import_data, dict):
                import_data = [import_data]
                json_data = json.dumps(import_data)

            # SAFETY CHECK: Financial Year for Vouchers (Pre-Queue)
            # This covers ALL voucher types (grouped list OR single dict)
            try:
                from database.financial_year_db import get_fy_by_date
                unique_dates = set()
                
                check_list = import_data if isinstance(import_data, list) else [import_data]
                for v in check_list:
                    d = v.get("date")
                    if d: unique_dates.add(d)
                
                for d in unique_dates:
                    if not get_fy_by_date(d, company_id=company_id):
                        print(f"Queue Check Blocked: No FY for {d}")
                        return jsonify({
                            "success": False, 
                            "message": f"CRITICAL ERROR: No Financial Year defined for date {d}. Please create the Financial Year first."
                        }), 400
            except Exception as e:
                # If checking fails (e.g. malformed data), we rely on later validation, 
                # but print error so we know safety check was skipped.
                print(f"FY Safety Check Skipped due to data format: {e}")

            is_valid, msg = validate_import_data(voucher_type, import_data, company_id=company_id)
            
            # Comprehensive Validation (DB Check)
            missing_ledgers_set = set()
            missing_items_set = set()
            missing_locations_set = set()
            missing_cost_centers_set = set()
            balance_errors = []
            linked_voucher_errors = []
            
            if is_valid and validation_status != "Failed":
                try:
                    existing_ledger_rows = get_ledgers(company_id=company_id)
                    existing_ledgers = {row['ledger_name'] for row in existing_ledger_rows}
                    
                    existing_item_rows = get_items(company_id=company_id)
                    existing_items = {row['name'] for row in existing_item_rows}
                    
                    existing_location_rows = get_locations(company_id=company_id)
                    existing_location_codes = {row['location_code'] for row in existing_location_rows}
                    existing_location_names = {row['location_name'] for row in existing_location_rows}
                    
                    existing_cc_rows = get_cost_centers(company_id=company_id)
                    existing_cost_centers = {row['center_code'] for row in existing_cc_rows} # center_code
                    # Also map names just in case import uses names? 
                    # Standard import usually uses names for ledgers/items but codes for CC? 
                    # utils.py checks for "center_code" and "center_name" in Master import.
                    # But in Voucher import, utils.py checks "cost_center" field. 
                    # Let's assume it matches center_code OR center_name.
                    existing_cc_names = {row['center_name'] for row in existing_cc_rows}

                    existing_vouchers = set()
                    if voucher_type in ["Additional Charge", "Purchase Return", "Sales Return"]:
                         conn_temp = get_db_connection()
                         try:
                             cur_temp = conn_temp.cursor()
                             # For Additional Charge: Linked voucher MUST have items
                             # For Returns: Ideally linked voucher should exist
                             # Enforce Company ID check
                             cur_temp.execute("SELECT DISTINCT voucher_number FROM item_entries WHERE company_id = ?", (company_id,))
                             existing_vouchers = {row[0] for row in cur_temp.fetchall()}
                         finally:
                             conn_temp.close()

                    for idx, voucher in enumerate(import_data):
                        # Check Ledger Entries
                        for le in voucher.get("ledger_entries", []):
                            lname = le.get("ledger_name")
                            if lname and lname not in existing_ledgers:
                                missing_ledgers_set.add(lname)
                            
                            cc = le.get("cost_center")
                            if cc and cc not in existing_cost_centers and cc not in existing_cc_names:
                                missing_cost_centers_set.add(cc)
                        
                        # Check Item Entries
                        for ie in voucher.get("item_entries", []):
                            iname = ie.get("item_name")
                            if iname and iname not in existing_items:
                                missing_items_set.add(iname)
                            
                            lname = ie.get("ledger_name") or ie.get("item_ledger_name")
                            if lname and lname not in existing_ledgers:
                                missing_ledgers_set.add(lname)
                            
                            iloc = ie.get("location_name")
                            if iloc and iloc not in existing_location_codes and iloc not in existing_location_names:
                                missing_locations_set.add(iloc)
                                
                            cc = ie.get("cost_center")
                            if cc and cc not in existing_cost_centers and cc not in existing_cc_names:
                                missing_cost_centers_set.add(cc)
                        
                        # Check party ledger (SKIP for Additional Charge as it is per-line now)
                        if voucher_type != "Additional Charge":
                            pl = voucher.get("party_ledger")
                            if pl and pl not in existing_ledgers:
                                missing_ledgers_set.add(pl)
                        else:
                            # For Additional Charge, check party_ledger inside charges
                            for ch in voucher.get("charges", []):
                                pl = ch.get("party_ledger")
                                if pl and pl not in existing_ledgers:
                                    missing_ledgers_set.add(pl)
                        
                        # Check voucher location
                        vl = voucher.get("location_name")
                        if vl and vl not in existing_location_codes and vl not in existing_location_names:
                            missing_locations_set.add(vl)
                            
                        # Check Header Cost Center
                        hcc = voucher.get("cost_center")
                        if hcc and hcc not in existing_cost_centers and hcc not in existing_cc_names:
                            missing_cost_centers_set.add(hcc)

                        # Check Linked Voucher (for Additional Charge and Purchase Return)
                        if voucher_type in ["Additional Charge", "Purchase Return", "Sales Return"]:
                             lvn = voucher.get("linked_voucher_number")
                             if lvn and lvn not in existing_vouchers:
                                 linked_voucher_errors.append(f"Linked Voucher '{lvn}' not found")

                        # Check Balance (Debit vs Credit)
                        # Skip for Physical Stock and Opening as they might be one-sided or auto-balanced later
                        if voucher_type not in ["Physical Stock", "Opening"]:
                            total_debit = 0.0
                            total_credit = 0.0
                            
                            for le in voucher.get("ledger_entries", []):
                                amt = float(le.get("amount", 0) or 0)
                                if le.get("type") == "Debit" or le.get("ledger_type") == "Debit":
                                    total_debit += amt
                                elif le.get("type") == "Credit" or le.get("ledger_type") == "Credit":
                                    total_credit += amt
                                
                            for ie in voucher.get("item_entries", []):
                                amt = float(ie.get("item_amount", 0) or 0)
                                t = ie.get("item_type") or ie.get("type")
                                if t == "Debit":
                                    total_debit += amt
                                elif t == "Credit":
                                    total_credit += amt
                                    
                            if abs(total_debit - total_credit) > 0.01:
                                balance_errors.append(f"Voucher {idx+1} (Date: {voucher.get('date')}) mismatch: Dr {total_debit:.2f} / Cr {total_credit:.2f}")

                    if missing_ledgers_set or missing_items_set or missing_locations_set or missing_cost_centers_set or balance_errors or linked_voucher_errors:
                        is_valid = False
                        details = []
                        if missing_ledgers_set:
                            details.append(f"Missing Ledgers: {len(missing_ledgers_set)}")
                        if missing_items_set:
                            details.append(f"Missing Items: {len(missing_items_set)}")
                        if missing_locations_set:
                            details.append(f"Missing Locations: {len(missing_locations_set)}")
                        if missing_cost_centers_set:
                            details.append(f"Missing Cost Centers: {len(missing_cost_centers_set)}")
                        if linked_voucher_errors:
                            details.append(f"Linked Vouchers Missing: {len(linked_voucher_errors)}")
                            details.append(f"Sample: {'; '.join(linked_voucher_errors[:2])}")
                        if balance_errors:
                            details.append(f"Balance Errors: {len(balance_errors)}")
                            # Append first few balance errors to msg
                            details.append(f"Sample: {'; '.join(balance_errors[:2])}")
                            
                        msg = "; ".join(details)
                except Exception as e:
                    print(f"Error during DB validation: {e}")

            if validation_status != "Failed":
                validation_status = "Success" if is_valid else "Failed"
            if not is_valid:
                validation_msg = msg
                
            print(
                f"Voucher validation for {voucher_type}: {validation_status} - {validation_msg}"
            )

        date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        file_name = f"{voucher_type}_{date}.json"

        # Prepare missing strings
        missing_ledger_str = ",".join(sorted(missing_ledgers_set)) if 'missing_ledgers_set' in locals() and missing_ledgers_set else None
        missing_item_str = ",".join(sorted(missing_items_set)) if 'missing_items_set' in locals() and missing_items_set else None

        conn = get_db_connection()
        cursor = conn.cursor()

        # Ensure schema is up to date (HOTFIX for missing columns in existing DBs)
        try:
            # PostgreSQL compatible schema check
            cursor.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'import_queue'
            """)
            existing_cols = [row[0] for row in cursor.fetchall()]
            if 'missing_ledger' not in existing_cols:
                cursor.execute("ALTER TABLE import_queue ADD COLUMN missing_ledger TEXT")
            if 'failure_reason' not in existing_cols:
                cursor.execute("ALTER TABLE import_queue ADD COLUMN failure_reason TEXT")
            if 'missing_item' not in existing_cols:
                cursor.execute("ALTER TABLE import_queue ADD COLUMN missing_item TEXT")
            conn.commit() 
        except Exception as e:
            print(f"Error checking/updating schema in queue_import: {e}")

        # company_id = get_current_company_id() # Already got it at top
        # company_id = get_current_company_id() # Already got it at top
        
        # Use helper for DB compatibility (Postgres RETURNING id vs SQLite lastrowid)
        from database.config import execute_insert_returning_id
        
        sql_insert = """
            INSERT INTO import_queue (
                company_id, date, file_name, voucher_type, json_data,
                validation_status, upload_status,
                missing_ledger, failure_reason, missing_item
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        params = (
            company_id,
            date,
            file_name,
            voucher_type,
            json_data,
            validation_status,
            "In Progress",
            missing_ledger_str,
            validation_msg,
            missing_item_str
        )
        
        new_id = execute_insert_returning_id(cursor, sql_insert, params)
        conn.commit()
        
        print(
            f"Inserted: id={new_id}, date={date}, "
            f"file_name={file_name}, voucher_type={voucher_type}, "
            f"validation_status={validation_status}, upload_status='In Progress'"
        )
        # Auto-processing disabled: processing happens only via upload_import from
        # the Import Queue page, to avoid duplicate items and Opening vouchers.
        if False and voucher_type == "Inventory" and validation_status == "Success":
            try:
                inv_list = import_data if isinstance(import_data, list) else [import_data]
                group_map = {g[1]: g[0] for g in get_inventory_groups(company_id=company_id)}
                company = get_company_settings(company_id=company_id)
                multiple_locations_enabled = bool(company and company.get('multiple_locations_applicable'))
                default_loc = get_default_location(company_id=company_id)
                fy = (company or {}).get('financial_year_start') or '01-01'
                today_year = datetime.now().year
                date_str = f"{today_year}-{fy}"
                
                # Reuse existing connection for atomic operation if possible, but here we are post-commit of queue.
                # We can start a new transaction for Inventory creation.
                
                c2_conn = get_db_connection()
                c2 = c2_conn.cursor()
                try:
                    for entry in inv_list:
                        item_code = entry.get("Item Code")
                        name = entry.get("Item Name")
                        group_name = entry.get("Group Name")
                        unit_code = entry.get("Unit")
                        unit_price = entry.get("Selling Price", entry.get("Unit Price", 0))
                        vat_rate = entry.get("VAT 5%", 0)
                        try:
                            unit_price = float(unit_price or 0)
                        except Exception:
                            unit_price = 0.0
                        try:
                            vat_rate = float(vat_rate or 0)
                        except Exception:
                            vat_rate = 0.0
                        stock_group_code = group_map.get(group_name)
                        if not all([item_code, name, stock_group_code, unit_code]):
                            continue
                        
                        # Pass connection to share transaction
                        add_inventory(item_code, name, stock_group_code, unit_code, unit_price, vat_rate, db_connection=c2_conn)
                        
                        opening_qty = entry.get("Opening Quantity")
                        opening_price = entry.get("Opening Price (Cost)")
                        try:
                            opening_qty = float(opening_qty) if opening_qty not in (None, "") else 0.0
                        except Exception:
                            opening_qty = 0.0
                        try:
                            opening_price = float(opening_price) if opening_price not in (None, "") else 0.0
                        except Exception:
                            opening_price = 0.0
                        if opening_qty > 0 and opening_price > 0:
                            final_location_name = None
                            if multiple_locations_enabled:
                                final_location_name = entry.get("Location Name") or (default_loc[2] if default_loc else "Main Location")
                            else:
                                final_location_name = "Main Location"
                            
                            c2.execute(
                                "UPDATE inventory SET stock_quantity = ?, opening_price = ?, opening_location_name = ? WHERE name = ? AND company_id = ?",
                                (opening_qty, opening_price, final_location_name, name, company_id),
                            )
                            
                            item_entries = [{
                                'item_name': name,
                                'quantity': opening_qty,
                                'unit_price': opening_price,
                                'ledger_name': 'Inventory',
                                'type': 'Debit',
                            }]
                            # Pass connection
                            add_voucher('Opening', date_str, ledger_entries=[], item_entries=item_entries, narration='Auto Opening from Import', location_name=final_location_name, db_connection=c2_conn)
                    c2_conn.commit()
                finally:
                    c2_conn.close()
            except Exception as e:
                print(f"Inventory auto-process error: {str(e)}")
        conn.close()

        return jsonify({"success": True, "message": "Import queued. Open the Import Queue to process it."})

    except sqlite3.Error as e:
        print(f"Database error in queue_import: {str(e)}")
        return jsonify(
            {"success": False, "message": f"Database error: {str(e)}"}
        ), 500

    except Exception as e:
        print(f"Unexpected error in queue_import: {str(e)}")
        return jsonify(
            {"success": False, "message": f"Server error: {str(e)}"}
        ), 500


@import_bp.route("/report/import-queue", methods=["GET"])
@login_required
def import_queue():
    try:
        company_id = get_current_company_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, date, file_name, voucher_type,
                   validation_status, upload_status,
                   missing_ledger, failure_reason, missing_item
            FROM import_queue
            WHERE company_id = ?
            ORDER BY date DESC
            """,
            (company_id,)
        )
        queue = cursor.fetchall()
        formatted_queue = []
        for q in queue:
            row = [
                q[0],
                format_date(q[1]),
                q[2],
                q[3],
                q[4],
                q[5],
                q[6],
                q[7],
                q[8] if len(q) > 8 else None
            ]
            formatted_queue.append(row)

        conn.close()

        print(f"Fetched queue data: {formatted_queue}")
        return render_template(
            "import_queue.html",
            queue=formatted_queue,
            username=current_user.username,
        )

    except sqlite3.Error as e:
        print(f"Error in import_queue: {str(e)}")
        return "Database unavailable", 500


@import_bp.route("/download_missing_ledgers", methods=["GET"])
@login_required
def download_missing_ledgers():
    """
    Collect all distinct missing ledger names from import_queue.missing_ledger
    and export them to an Excel file with one column: Ledger Name.
    Optional: ?id=<queue_id> to filter by specific import.
    """
    try:
        queue_id = request.args.get("id")
        company_id = get_current_company_id()
        conn = get_db_connection()
        cursor = conn.cursor()

        if queue_id:
            cursor.execute(
                """
                SELECT missing_ledger
                FROM import_queue
                WHERE id = ? AND company_id = ? AND missing_ledger IS NOT NULL AND TRIM(missing_ledger) <> ''
                """,
                (queue_id, company_id)
            )
        else:
            cursor.execute(
                """
                SELECT missing_ledger
                FROM import_queue
                WHERE company_id = ?
                AND missing_ledger IS NOT NULL
                AND TRIM(missing_ledger) <> ''
                """,
                (company_id,)
            )
            
        rows = cursor.fetchall()
        conn.close()

        missing_set = set()
        for row in rows:
            text = row[0] or ""
            parts = [p.strip() for p in text.split(",") if p.strip()]
            for p in parts:
                missing_set.add(p)
                
        if not missing_set:
             # If filtering by ID and nothing found, maybe return a message or empty excel?
             # Let's return empty excel but print log
             print(f"No missing ledgers found for request (id={queue_id})")

        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {"in_memory": True})
        ws = workbook.add_worksheet("Missing Ledgers")

        ws.write(0, 0, "Ledger Name")
        for row_idx, name in enumerate(sorted(missing_set), start=1):
            ws.write(row_idx, 0, name)

        workbook.close()
        output.seek(0)
        
        filename = f"Missing_Ledgers_{queue_id}.xlsx" if queue_id else "Missing_Ledgers_All.xlsx"

        print(f"Downloaded missing ledgers: {len(missing_set)} names")
        return send_file(
            output,
            download_name=filename,
            as_attachment=True,
        )

    except sqlite3.Error as e:
        print(f"Database error in download_missing_ledgers: {str(e)}")
        return "Database error while generating missing ledger list", 500

    except Exception as e:
        print(f"Error in download_missing_ledgers: {str(e)}")
        return "Error while generating missing ledger list", 500


@import_bp.route("/download_missing_items", methods=["GET"])
@login_required
def download_missing_items():
    """
    Collect all distinct missing item names from import_queue.missing_item
    and export them to an Excel file with one column: Item Name.
    Optional: ?id=<queue_id> to filter by specific import.
    """
    try:
        queue_id = request.args.get("id")
        company_id = get_current_company_id()
        conn = get_db_connection()
        cursor = conn.cursor()

        if queue_id:
            cursor.execute(
                """
                SELECT missing_item
                FROM import_queue
                WHERE id = ? AND company_id = ? AND missing_item IS NOT NULL AND TRIM(missing_item) <> ''
                """,
                (queue_id, company_id)
            )
        else:
            cursor.execute(
                """
                SELECT missing_item
                FROM import_queue
                WHERE company_id = ?
                AND missing_item IS NOT NULL
                AND TRIM(missing_item) <> ''
                """,
                (company_id,)
            )
        rows = cursor.fetchall()
        conn.close()

        missing_set = set()
        for row in rows:
            text = row[0] or ""
            parts = [p.strip() for p in text.split(",") if p.strip()]
            for p in parts:
                missing_set.add(p)

        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {"in_memory": True})
        ws = workbook.add_worksheet("Missing Items")

        ws.write(0, 0, "Item Name")
        for row_idx, name in enumerate(sorted(missing_set), start=1):
            ws.write(row_idx, 0, name)

        workbook.close()
        output.seek(0)

        filename = f"Missing_Items_{queue_id}.xlsx" if queue_id else "Missing_Items_All.xlsx"

        print(f"Downloaded missing items: {len(missing_set)} names")
        return send_file(
            output,
            download_name=filename,
            as_attachment=True,
        )

    except sqlite3.Error as e:
        print(f"Database error in download_missing_items: {str(e)}")
        return "Database error while generating missing item list", 500

    except Exception as e:
        print(f"Error in download_missing_items: {str(e)}")
        return "Error while generating missing item list", 500


@import_bp.route("/delete_import/<int:id>", methods=["DELETE"])
@login_required
def delete_import(id):
    try:
        company_id = get_current_company_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM import_queue WHERE id = ? AND company_id = ?", (id, company_id))

        if cursor.rowcount == 0:
            print(f"Delete failed: No entry found for id={id}")
            conn.close()
            return jsonify(
                {"success": False, "message": "Queue entry not found"}
            ), 404

        conn.commit()
        print(f"Deleted queue entry: id={id}")
        conn.close()

        return jsonify(
            {"success": True, "message": "Queue entry deleted"}
        )

    except sqlite3.Error as e:
        print(f"Error deleting queue entry id={id}: {str(e)}")
        return jsonify(
            {
                "success": False,
                "message": f"Error deleting entry: {str(e)}",
            }
        ), 500


def _mark_queue_failed(queue_id, reason, missing_ledger=None):
    """Record an upload failure on the queue row so the Import Queue page shows
    the real reason (and missing ledgers stay downloadable)."""
    try:
        c = get_db_connection()
        cur = c.cursor()
        if missing_ledger:
            cur.execute(
                "UPDATE import_queue SET upload_status = 'Failed', failure_reason = ?, missing_ledger = ? WHERE id = ?",
                (str(reason)[:2000], missing_ledger, queue_id),
            )
        else:
            cur.execute(
                "UPDATE import_queue SET upload_status = 'Failed', failure_reason = ? WHERE id = ?",
                (str(reason)[:2000], queue_id),
            )
        c.commit()
        c.close()
    except Exception as e:
        print(f"Could not record failure for queue id={queue_id}: {e}")


@import_bp.route("/upload_import/<int:id>", methods=["POST"])
@login_required
def upload_import(id):
    company_id = get_current_company_id()
    conn = None
    try:
        # 1. Get Queue Entry
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT json_data, voucher_type, validation_status FROM import_queue WHERE id = ? AND company_id = ?",
            (id, company_id),
        )
        result = cursor.fetchone()

        if not result:
            print(f"Upload failed: No entry found for id={id}")
            conn.close()
            return jsonify(
                {"success": False, "message": "Queue entry not found"}
            ), 404

        json_data, import_type, validation_status = result
        print(
            f"Uploading queue entry id={id}: "
            f"json_data_len={len(json_data)}, import_type={import_type}, status={validation_status}"
        )

        # Entries that failed validation must not be processed — fix the file
        # and upload it again (the failure reason is shown in the queue).
        if validation_status == "Failed":
            conn.close()
            return jsonify({
                "success": False,
                "message": "This entry failed validation and cannot be uploaded. See the Failure Reason column, fix the file, and upload it again.",
            }), 400

        import_data = json.loads(json_data)
        
        # SAFETY CHECK: Financial Year for Vouchers
        # Ensure we don't start a transaction if FY is missing for any voucher
        if import_type not in ["Group", "Ledger", "Cost Center", "Inventory Group", "Sub Group", "Inventory"]:
            from database.financial_year_db import get_fy_by_date
            unique_dates = set()
            
            check_list = import_data if isinstance(import_data, list) else [import_data]
            for v in check_list:
                d = v.get("date")
                if d: unique_dates.add(d)
            
            for d in unique_dates:
                if not get_fy_by_date(d, company_id=company_id):
                    conn.close()
                    reason = f"No Financial Year defined for date {d}. Please create the Financial Year first."
                    _mark_queue_failed(id, reason)
                    return jsonify({"success": False, "message": reason}), 400

        # Re-validate Inventory imports: mandatory opening fields, Balancing
        # Ledger existence, and Financial Year coverage for Purchase Dates.
        # Failures are written back to the queue row so the reason is visible
        # and missing ledgers remain downloadable.
        if import_type == "Inventory":
            from database.financial_year_db import get_fy_by_date as _get_fy_by_date
            ledger_names = {row['ledger_name'] for row in get_ledgers(company_id=company_id)}
            missing_bal_ledgers = set()
            inv_errors = []
            check_list = import_data if isinstance(import_data, list) else [import_data]
            for item in check_list:
                try:
                    oq = float(item.get("Opening Quantity") or 0)
                except (ValueError, TypeError):
                    oq = 0.0
                pd_raw = str(item.get("Purchase Date", "") or "").strip()
                if oq > 0:
                    if not pd_raw:
                        inv_errors.append(f"Purchase Date is required for item '{item.get('Item Name')}' when Opening Quantity > 0")
                        continue
                    bl = str(item.get("Balancing Ledger", "") or "").strip()
                    if not bl:
                        inv_errors.append(f"Balancing Ledger is required for item '{item.get('Item Name')}' when Opening Quantity > 0. Please use the updated template.")
                        continue
                    if bl not in ledger_names:
                        missing_bal_ledgers.add(bl)
                # Any provided Purchase Date must be valid and inside a defined
                # Financial Year, even for rows with zero Opening Quantity.
                if pd_raw or oq > 0:
                    pd_norm = parse_date(pd_raw) if pd_raw else None
                    # parse_date returns the original string on failure, so
                    # verify the result really is YYYY-MM-DD
                    try:
                        datetime.strptime(str(pd_norm), "%Y-%m-%d")
                    except (ValueError, TypeError):
                        pd_norm = None
                    if not pd_norm:
                        inv_errors.append(f"Invalid Purchase Date '{pd_raw}' for item '{item.get('Item Name')}'")
                    elif not _get_fy_by_date(pd_norm, company_id=company_id):
                        inv_errors.append(f"No Financial Year defined for Purchase Date {pd_norm} (item '{item.get('Item Name')}')")
            if missing_bal_ledgers:
                inv_errors.insert(0, "Missing Balancing Ledgers: " + ", ".join(sorted(missing_bal_ledgers)) + ". Create these ledgers first (you can download the list from the Import Queue).")
            if inv_errors:
                reason = "; ".join(inv_errors[:10])
                conn.close()
                _mark_queue_failed(id, reason, missing_ledger=",".join(sorted(missing_bal_ledgers)) if missing_bal_ledgers else None)
                return jsonify({"success": False, "message": reason}), 400
        
        # START ATOMIC TRANSACTION
        # conn is already open. We will use it for all inserts.
        # If any exception occurs, we rollback.
        
        message = ""
        success_count = 0
        affected_items = set()
        earliest_date = None

        # ---------- MASTER IMPORTS ----------
        if import_type == "Group":
            for group in import_data:
                add_group(
                    group.get("group_code"),
                    group["group_name"],
                    group["nature"],
                    db_connection=conn,
                    company_id=company_id
                )
            message = "Groups uploaded successfully!"

        elif import_type == "Ledger":
            # Normalize group names for robust matching
            # We need to fetch groups inside the transaction or use existing
            group_vals = get_groups(company_id=company_id)
            group_map = { (g['group_name'] or "").strip().lower(): g['group_code'] for g in group_vals }

            for ledger in import_data:
                gname_raw = ledger.get("group_name")
                gname_key = (gname_raw or "").strip().lower()
                group_code = group_map.get(gname_key)
                # Validation check removed as per user request (done in queue_import)
                
                add_ledger(
                    ledger["ledger_code"],
                    ledger["ledger_name"],
                    group_code,
                    round(float(ledger["opening_balance"]), 2),
                    ledger["opening_balance_type"],
                    db_connection=conn,
                    company_id=company_id
                )
            message = "Ledgers uploaded successfully!"

        elif import_type == "Cost Center":
            for center in import_data:
                add_cost_center(
                    center["center_code"],
                    center["center_name"],
                    db_connection=conn,
                    company_id=company_id
                )
            message = "Cost Centers uploaded successfully!"

        elif import_type == "Sub Group":
            # Map Groups
            group_vals = get_groups(company_id=company_id)
            # Map "Group Name" -> "Group Code"
            group_map = { (g['group_name'] or "").strip().lower(): g['group_code'] for g in group_vals }
            
            from database import add_sub_group
            
            for row in import_data:
                sub_name = row.get("Sub Group Name")
                # Handle both likely column names for Parent
                parent_name = row.get("Parent Group Name") or row.get("Parent Group") or ""
                
                parent_code = group_map.get(parent_name.strip().lower())
                
                if not sub_name or not parent_code:
                    print(f"Skipping sub group {sub_name}: Parent '{parent_name}' not found.")
                    continue
                    
                add_sub_group(
                    sub_name,
                    parent_code,
                    db_connection=conn,
                    company_id=company_id
                )
            message = "Sub Groups uploaded successfully!"

        elif import_type == "Inventory Group":
            for group in import_data:
                add_inventory_group(
                    group["group_code"],
                    group["group_name"],
                    db_connection=conn,
                    company_id=company_id
                )
            message = "Inventory Groups uploaded successfully!"

        elif import_type == "Inventory":
            # Map groups
            group_map = {g['group_name']: g['group_code'] for g in get_inventory_groups(company_id=company_id)}
            
            # Prepare batch data
            inventory_batch_items = []
            opening_entries_by_group = {} # Key: (date, location_name), Value: list of item_entries
            
            from database.inventory_db import add_inventory_batch

            for item in import_data:
                stock_group_code = group_map.get(item["Group Name"])
                # Validation check removed as per user request (done in queue_import)

                price = item.get("Selling Price", item.get("Unit Price", 0)) or 0
                vat_rate = item.get("VAT 5%", 0)
                
                # Prepare inventory item data
                inv_item = {
                    'item_code': item["Item Code"],
                    'name': item["Item Name"],
                    'stock_group_code': stock_group_code,
                    'unit_code': item["Unit"],
                    'unit_price': round(float(price), 2) if price not in (None, "") else 0.0,
                    'vat_rate': round(float(vat_rate), 2) if vat_rate not in (None, "") else 0.0
                }
                inventory_batch_items.append(inv_item)

                # Optional opening fields
                opening_qty_raw = item.get("Opening Quantity", "")
                opening_price_raw = item.get("Opening Price (Cost)", "")
                location_name_form = item.get("Location", "")
                balancing_ledger = str(item.get("Balancing Ledger", "") or "").strip()

                try:
                    opening_qty = float(opening_qty_raw) if opening_qty_raw not in (None, "") else 0.0
                except (ValueError, TypeError):
                    opening_qty = 0.0
                
                opening_price = None
                if opening_price_raw not in (None, ""):
                    try:
                        opening_price = round(float(opening_price_raw), 2)
                    except (ValueError, TypeError):
                        opening_price = None

                if opening_qty > 0:
                    company = get_company_settings(company_id=company_id)
                    multiple_locations_enabled = bool(company and company.get('multiple_locations_applicable'))
                    default_loc = get_default_location(company_id=company_id)
                    if multiple_locations_enabled:
                        final_location_name = location_name_form or (default_loc[2] if default_loc else "Main Location")
                    else:
                        final_location_name = "Main Location"

                    # Determine opening date — prefer "Purchase Date", fall back to "Opening Date"
                    opening_date_input = item.get("Purchase Date") or item.get("Opening Date")
                    
                    # Calculate default date (Start of FY)
                    today_year = datetime.now().year
                    fy_start = (company or {}).get('financial_year_start') or '01-01'
                    default_date_str = f"{today_year}-{fy_start}"
                    
                    final_date_str = parse_date(opening_date_input) if opening_date_input else default_date_str
                    if not final_date_str:
                         final_date_str = default_date_str
                    
                    # Group key includes balancing_ledger so each unique combo gets its own voucher
                    key = (final_date_str, final_location_name, balancing_ledger)
                    if key not in opening_entries_by_group:
                        opening_entries_by_group[key] = []

                    opening_entries_by_group[key].append({
                        'item_name': item["Item Name"],
                        'quantity': opening_qty,
                        'unit_price': opening_price,
                        'ledger_name': 'Inventory',
                        'type': 'Debit',
                    })
                    
                    affected_items.add(item["Item Name"])
                    if not earliest_date or final_date_str < earliest_date:
                        earliest_date = final_date_str

            # 1. Batch Add/Update Inventory Items
            try:
                add_inventory_batch(inventory_batch_items, company_id=company_id, db_connection=conn)
            except Exception as e:
                print(f"Batch add inventory failed: {e}")
                raise e

            # 2. Process Opening Vouchers (Grouped by date + location + balancing ledger)
            success_vouchers = 0
            for (date, loc, bal_ledger), entries in opening_entries_by_group.items():
                try:
                    # Pre-compute group total to build the balancing credit entry
                    group_total = round(sum(
                        float(e['quantity'] or 0) * float(e['unit_price'] or 0)
                        for e in entries
                    ), 2)
                    credit_entries = []
                    if bal_ledger and group_total > 0:
                        credit_entries = [{'ledger_name': bal_ledger, 'amount': group_total, 'type': 'Credit'}]

                    add_voucher(
                        voucher_type='Opening',
                        date=date,
                        ledger_entries=credit_entries,
                        item_entries=entries,
                        narration='Auto Opening from Import',
                        location_name=loc,
                        db_connection=conn,
                        company_id=company_id,
                        skip_recalc=True
                    )
                    success_vouchers += 1
                except Exception as e:
                    print(f"Failed to create opening voucher for date {date}, loc {loc}: {e}")
                    raise e
                    
            message = f"Inventory items uploaded successfully! Created {success_vouchers} Opening Vouchers."

        else:
            # ---------- VOUCHER IMPORTS ----------
            if import_type == "Additional Charge":
                 # Special handling for Additional Charge
                 from database.vouchers_db import add_additional_charges_voucher
                 
                 for voucher in import_data:
                     add_additional_charges_voucher(
                         voucher["date"],
                         voucher["linked_voucher_number"],
                         voucher["charges"],
                         narration=voucher["narration"],
                         db_connection=conn,
                         skip_recalc=True
                     )
                     
                     # Collect affected items for batch recalculation
                     linked_vn = voucher["linked_voucher_number"]
                     cursor.execute("""
                        SELECT DISTINCT ie.item_name, v.date
                        FROM item_entries ie
                        JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
                        WHERE v.voucher_number = ? AND v.company_id = ?
                     """, (linked_vn, company_id))
                     
                     for row in cursor.fetchall():
                         affected_items.add(row[0])
                         v_date = row[1]
                         if not earliest_date or v_date < earliest_date:
                             earliest_date = v_date

                     success_count += 1
                 
                 message = f"Uploaded {success_count} Additional Charge vouchers successfully!"
            
            else:
                # Standard Voucher Processing
                if isinstance(import_data, dict):
                    import_data = [import_data]
                
                # We skip validation logic here as per user request (Validation Success -> No Fail)
                # We proceed directly to insertion.
                
                from database import calculate_weighted_average_price, recalculate_running_balance_for_item, recompute_ledger_closing_balances

                for voucher in import_data:
                    # Collect items for batch recalculation
                    v_date = voucher.get("date")
                    if not earliest_date or v_date < earliest_date:
                        earliest_date = v_date
                    for ie in voucher.get("item_entries", []):
                        if ie.get("item_name"):
                            affected_items.add(ie.get("item_name"))

                    # Handle WAP for Stock Adjustment / Transfer (same logic as before)
                    if import_type == "Stock Adjustment":
                        date = voucher.get("date")
                        for ie in voucher.get("item_entries", []):
                             item_name = ie.get("item_name")
                             if item_name:
                                 wap = calculate_weighted_average_price(item_name, date)
                                 qty = float(ie.get("quantity") or 0)
                                 amount_wap = round(abs(qty) * wap, 2)
                                 ie["unit_price"] = wap
                                 ie["item_amount"] = amount_wap
                
                    elif import_type == "Inventory Transfer":
                        date = voucher.get("date")
                        item_entries = voucher.get("item_entries", [])
                        for ie in item_entries:
                            if ie.get("item_type") == "Credit": # Source
                                 item_name = ie.get("item_name")
                                 wap = calculate_weighted_average_price(item_name, date)
                                 ie["unit_price"] = wap
                                 ie["item_amount"] = round(abs(float(ie.get("quantity") or 0)) * wap, 2)
                            elif ie.get("item_type") == "Debit": # Dest
                                 item_name = ie.get("item_name")
                                 wap = calculate_weighted_average_price(item_name, date)
                                 ie["unit_price"] = wap
                                 ie["item_amount"] = round(abs(float(ie.get("quantity") or 0)) * wap, 2)
                        
                        # Sync ledger amounts
                        if len(voucher.get("item_entries", [])) == len(voucher.get("ledger_entries", [])):
                             for i, ie in enumerate(voucher["item_entries"]):
                                 le = voucher["ledger_entries"][i]
                                 le["amount"] = ie["item_amount"]

                    # Cleanup legacy keys and resolve Cost Centers
                    for entry in voucher["ledger_entries"]:
                        if "ledger_type" in entry: entry["type"] = entry.pop("ledger_type")
                        if entry.get("cost_center") and not entry.get("cost_center_code"):
                            entry["cost_center_code"] = get_cost_center_code(entry["cost_center"], company_id=company_id)

                    for entry in voucher.get("item_entries", []):
                        if "item_type" in entry: entry["type"] = entry.pop("item_type")
                        if "item_amount" in entry: entry["amount"] = entry.pop("item_amount")
                        if "item_ledger_name" in entry: entry["ledger_name"] = entry.pop("item_ledger_name")
                        if entry.get("cost_center") and not entry.get("cost_center_code"):
                            entry["cost_center_code"] = get_cost_center_code(entry["cost_center"], company_id=company_id)
                
                    # Resolve Cost Centers
                    header_cc_code = voucher.get("cost_center_code") # If already resolved? Or resolve from name
                    if not header_cc_code and voucher.get("cost_center"):
                         header_cc_code = get_cost_center_code(voucher["cost_center"], company_id=company_id)
                    
                    # Note: add_voucher expects codes if possible, or names?
                    # add_voucher signature: cost_center_code=None.
                    # We should ensure we pass code.
                    
                    add_voucher(
                        import_type,
                        voucher["date"],
                        voucher["ledger_entries"],
                        voucher.get("item_entries", []),
                        header_cc_code,
                        narration=voucher.get("narration", ""),
                        location_name=voucher.get("location_name"),
                        credit_days=voucher.get("credit_days"),
                        due_date=voucher.get("due_date"),
                        original_invoice_date=voucher.get("original_invoice_date"),
                        original_invoice_ref=voucher.get("original_invoice_ref"),
                        skip_recalc=True,  # Optimize: Skip per-voucher recalc
                        db_connection=conn # SHARED CONNECTION
                    )
                    success_count += 1

                message = f"Uploaded {success_count} vouchers successfully!"

        # Delete queue entry INSIDE transaction
        cursor.execute("DELETE FROM import_queue WHERE id = ? AND company_id = ?", (id, company_id))
        
        # COMMIT THE TRANSACTION
        conn.commit()
        
        # Close connection (we are done with atomic part)
        conn.close()
        conn = None 

        # Post-Processing: Recalculate Running Balances (if any items affected)
        # This runs in its own connection(s).
        recalc_warning = ""
        if affected_items:
            print(f"Batch recalculating running balances for {len(affected_items)} items from {earliest_date}...")
            try:
                from database import recalculate_running_balance_for_item, recompute_ledger_closing_balances
                for item_name in affected_items:
                    recalculate_running_balance_for_item(item_name, earliest_date)
            
                # Ensure Ledger Closing Balances are synced
                recompute_ledger_closing_balances()
            
            except Exception as e:
                print(f"Error in batch recalculation: {e}")
                recalc_warning = f" (Warning: Stock recalc error: {str(e)})"

        print(
            "Uploaded and deleted queue entry: "
            f"id={id}, message={message}"
        )

        return jsonify({"success": True, "message": message + recalc_warning})

    except sqlite3.Error as e:
        print(f"Database error uploading id={id}: {str(e)}")
        if conn:
            conn.rollback()
            conn.close()
        _mark_queue_failed(id, f"Upload error: {str(e)}")
        return jsonify(
            {"success": False, "message": f"Error uploading: {str(e)}"}
        ), 500

    except Exception as e:
        print(f"Error uploading id={id}: {str(e)}")
        if conn:
            conn.rollback()
            conn.close()
        _mark_queue_failed(id, f"Upload error: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 400


@import_bp.route("/repair_stock_gl", methods=["GET"])
@login_required
def repair_stock_gl():
    """
    Manual trigger to repair Stock GL entries and Closing Balances.
    Useful if data is out of sync (e.g. missing COGS entries).
    """
    try:
        from database import get_items, recalculate_running_balance_for_item, recompute_ledger_closing_balances
        
        # 1. Get all items
        items = get_items()
        item_names = [i[1] for i in items]
        print(f"Repair Stock GL: Found {len(item_names)} items.")
        
        # 2. Recalculate each item (inserts missing GL entries)
        success_count = 0
        errors = []
        for name in item_names:
            try:
                recalculate_running_balance_for_item(name)
                success_count += 1
            except Exception as e:
                errors.append(f"{name}: {str(e)}")
        
        # 3. Recompute Ledger Closing Balances
        recompute_ledger_closing_balances()
        
        msg = f"Repaired {success_count}/{len(item_names)} items. Ledger balances recomputed."
        if errors:
            msg += f" Errors: {'; '.join(errors)}"
            
        return jsonify({"success": True, "message": msg})
        
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@import_bp.route("/diagnose_inventory", methods=["GET"])
@login_required
def diagnose_inventory():
    """
    Diagnostic tool to identify discrepancies between Stock Report and Trial Balance.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        results = {}

        company_id = get_current_company_id()

        # 1. Check Inventory Ledger Closing Balance
        cursor.execute("SELECT closing_balance FROM ledgers WHERE ledger_name='Inventory' AND company_id = ?", (company_id,))
        row = cursor.fetchone()
        inv_ledger_bal = row[0] if row else 0.0
        results['tb_inventory_balance'] = inv_ledger_bal

        # 2. Check Total Stock Value
        cursor.execute("SELECT SUM(stock_value) FROM inventory WHERE company_id = ?", (company_id,))
        row = cursor.fetchone()
        stock_val = row[0] if row and row[0] is not None else 0.0
        results['stock_report_value'] = stock_val

        results['difference'] = stock_val - inv_ledger_bal

        # 3. Compare Item Entries vs Ledger Entries per Voucher
        cursor.execute("""
            SELECT ie.voucher_number, v.voucher_type, SUM(ie.amount) as item_total, ie.type
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE ie.company_id = ?
            GROUP BY ie.voucher_number, v.voucher_type, ie.type
        """, (company_id,))
        item_vouchers = cursor.fetchall()

        cursor.execute("""
            SELECT voucher_number, SUM(amount) as ledger_total, type
            FROM ledger_entries
            WHERE ledger_name = 'Inventory' AND company_id = ?
            GROUP BY voucher_number, type
        """, (company_id,))
        ledger_vouchers = cursor.fetchall()
        
        ledger_map = {}
        for v in ledger_vouchers:
            ledger_map[(v[0], v[2])] = v[1]

        discrepancies = []
        
        for v in item_vouchers:
            v_no, v_type, i_amt, i_type = v
            l_amt = ledger_map.get((v_no, i_type), 0.0)
            
            if abs(i_amt - l_amt) > 0.01:
                discrepancies.append({
                    "voucher": v_no,
                    "type": v_type,
                    "entry_type": i_type,
                    "item_amount": i_amt,
                    "ledger_amount": l_amt,
                    "diff": i_amt - l_amt
                })
        
        # Sort by diff descending
        discrepancies.sort(key=lambda x: abs(x['diff']), reverse=True)
        results['discrepancies_count'] = len(discrepancies)
        results['top_discrepancies'] = discrepancies[:50]
        
        conn.close()
        return jsonify(results)

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


def _group_voucher_rows(voucher_type, rows, company_id=None):
    from itertools import groupby
    from datetime import datetime, timedelta

    def parse_excel_date(date_val):
        if not date_val:
            return datetime.today().strftime("%Y-%m-%d")
        # If it's a number (Excel serial date)
        if isinstance(date_val, (int, float)):
            try:
                # Excel base date: Dec 30, 1899
                dt = datetime(1899, 12, 30) + timedelta(days=date_val)
                return dt.strftime("%Y-%m-%d")
            except:
                pass
        # If string, try common formats
        s = str(date_val).strip()
        for fmt in [
            "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y",
            "%Y.%m.%d", "%d.%m.%Y", "%d-%m-%y", "%m-%d-%y",
            "%Y/%m/%d"
        ]:
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except:
                pass
        return s

    # Ensure rows are sorted by Voucher Group ID for groupby
    def get_group_id(row):
        return str(row.get("Voucher Group ID", "")).strip()
        
    rows.sort(key=get_group_id)

    grouped_vouchers = []
    
    for group_id, group_rows in groupby(rows, key=get_group_id):
        if not group_id:
            continue # Skip rows without Group ID

        group_list = list(group_rows)
        first_row = group_list[0]
        first_date = parse_excel_date(first_row.get("Date"))

        # Check for date consistency within the group
        for i, row in enumerate(group_list):
            current_date = parse_excel_date(row.get("Date"))
            if current_date != first_date:
                raise ValueError(f"Date mismatch in Voucher Group ID '{group_id}'. All rows must have the same date ({first_date}). Found '{current_date}' at row index {i+1}.")
        
        if voucher_type == "Additional Charge":
            # Special structure for Additional Charge
            voucher = {
                "date": first_date,
                "narration": first_row.get("Narration", ""),
                # "party_ledger": first_row.get("Party Ledger Name"),  <-- REMOVED: Party Ledger is now per line
                "linked_voucher_number": first_row.get("Linked Purchase Voucher"),
                "charges": []
            }
            
            for row in group_list:
                amt = float(row.get("Amount", 0) or 0)
                method = row.get("Valuation Method")
                # Normalize method
                if method:
                    method_l = method.lower()
                    if "val" in method_l: method = "Value"
                    elif "qty" in method_l or "quantity" in method_l: method = "Quantity"
                    elif "weight" in method_l: method = "Weight (KG)"
                else:
                    method = "Value" # Default
                    
                charge_narration = row.get("Narration", "") # Line narration if needed
                
                vat_amount = float(row.get("VAT Amount", 0) or 0)
                
                party_ledger_line = row.get("Party Ledger Name") or row.get("Ledger Name")

                voucher["charges"].append({
                    "amount": amt,
                    "valuation_method": method,
                    "narration": charge_narration,
                    "vat_amount": vat_amount,
                    "party_ledger": party_ledger_line
                })
            
            grouped_vouchers.append(voucher)
            continue

        voucher = {
            "date": first_date,
            "narration": first_row.get("Narration", ""),
            "location_name": first_row.get("Location", ""),
            "cost_center": first_row.get("Cost Center", ""),
            "credit_days": first_row.get("Credit Days"),
            "original_invoice_ref": first_row.get("Reference Number"),
            "original_invoice_date": parse_excel_date(first_row.get("Invoice Date")) if first_row.get("Invoice Date") else None,
            "ledger_entries": [],
            "item_entries": []
        }

        # Invoice Date is mandatory for these voucher types
        if voucher_type in ["Purchase", "Purchase Return", "Expense"] and not voucher["original_invoice_date"]:
            raise ValueError(
                f"Invoice Date is required for {voucher_type} vouchers. "
                f"Missing in Voucher Group ID '{group_id}'."
            )
        
        if voucher_type in ["Sales", "Purchase", "Sales Return", "Purchase Return", "Expense"]:
            # Handle Credit Days and Due Date
            credit_days_val = voucher.get("credit_days")
            
            # --- Auto-fetch Credit Days if missing ---
            if not credit_days_val and voucher_type in ["Sales", "Purchase", "Expense"]:
                 party_name = first_row.get("Party Ledger Name") or first_row.get("Ledger Name")
                 if party_name:
                     try:
                         from accounting_app import get_db_connection
                         conn_cd = get_db_connection()
                         cur_cd = conn_cd.cursor()
                         if company_id:
                             cur_cd.execute("SELECT credit_days FROM ledgers WHERE ledger_name = ? AND company_id = ? LIMIT 1", (party_name, company_id))
                         else:
                             cur_cd.execute("SELECT credit_days FROM ledgers WHERE ledger_name = ? LIMIT 1", (party_name,))
                         row = cur_cd.fetchone()
                         if row and row[0]:
                             credit_days_val = row[0]
                         conn_cd.close()
                     except Exception as e:
                         print(f"Import Queue: Error fetching credit days for {party_name}: {e}")
            # -----------------------------------------

            if credit_days_val and voucher_type in ["Sales", "Purchase", "Expense"]:
                try:
                    credit_days = int(credit_days_val)
                    from datetime import datetime, timedelta
                    # first_date is already YYYY-MM-DD string from parse_excel_date
                    voucher_date_obj = datetime.strptime(first_date, '%Y-%m-%d')
                    due_date_obj = voucher_date_obj + timedelta(days=credit_days)
                    voucher["due_date"] = due_date_obj.strftime('%Y-%m-%d')
                    voucher["credit_days"] = credit_days
                except (ValueError, TypeError):
                    voucher["credit_days"] = None
                    voucher["due_date"] = None
            else:
                 voucher["credit_days"] = None
                 voucher["due_date"] = None
        
        if voucher_type in ["Sales", "Purchase", "Sales Return", "Purchase Return"]:
            # Inventory Vouchers logic matching manual entry:
            # 1. Party Ledger (Total Amount)
            # 2. Items (Qty * Rate) -> triggers automatic Inventory/COGS/WAP updates in add_voucher
            # 3. VAT (if applicable)
            
            party_ledger_name = first_row.get("Party Ledger Name") or first_row.get("Ledger Name") # Party
            
            total_item_amount = 0.0
            total_vat_amount = 0.0
            total_discount_amount = 0.0
            
            for row in group_list:
                # Item Entry
                qty = float(row.get("Quantity", 0) or 0)
                rate = float(row.get("Rate", 0) or 0)
                amount = round(qty * rate, 2)
                
                # Determine Item Ledger and Type based on voucher type
                if voucher_type == "Sales":
                    item_ledger = row.get("Sales Ledger") or "Sales"
                    item_type = "Credit"
                    party_type = "Debit"
                elif voucher_type == "Sales Return":
                    item_ledger = row.get("Sales Ledger") or "Sales Return"
                    item_type = "Debit"
                    party_type = "Credit"
                elif voucher_type == "Purchase":
                    item_ledger = "Inventory" 
                    item_type = "Debit"
                    party_type = "Credit"
                elif voucher_type == "Purchase Return":
                    item_ledger = "Inventory"
                    item_type = "Credit"
                    party_type = "Debit"
                
                item_entry = {
                    "item_name": row.get("Item Name"),
                    "quantity": qty,
                    "unit_price": rate,
                    "item_amount": amount,
                    "item_ledger_name": item_ledger, # This ledger will be used for Revenue/Expense booking
                    "item_type": item_type,
                    "cost_center": row.get("Cost Center", ""),
                    "weight_kg": float(row.get("Weight (KG)") or 0)
                }
                voucher["item_entries"].append(item_entry)
                total_item_amount += amount
                
                # VAT Entry (if any)
                vat_rate = float(row.get("VAT %", 0) or 0)
                if vat_rate > 0:
                    vat_amount = round(amount * (vat_rate / 100), 2)
                    total_vat_amount += vat_amount
                    
                    vat_ledger = row.get("VAT Ledger")
                    if not vat_ledger:
                        if voucher_type == "Purchase":
                            vat_ledger = "Input VAT 5%"
                        elif voucher_type == "Sales":
                            vat_ledger = "Output VAT 5%"

                    if vat_ledger:
                         # VAT Type matches Party Type usually? 
                         # Sales (Credit Income) -> Output VAT (Credit Liability) -> Wait.
                         # Sales: Party Dr, Sales Cr, Output VAT Cr.
                         # Purchase: Party Cr, Purchase Dr, Input VAT Dr.
                         
                         vat_type = item_type # Same as item (Sales=Cr, Purchase=Dr)
                         
                         voucher["ledger_entries"].append({
                             "ledger_name": vat_ledger,
                             "amount": vat_amount,
                             "ledger_type": vat_type
                         })

                # Discount Entry (if any)
                discount_val = round(float(row.get("Discount Amount", 0) or 0), 2)
                if discount_val > 0:
                    total_discount_amount += discount_val
                    
                    # Determine Discount Ledger and Type
                    if voucher_type == "Sales":
                        disc_ledger = "Discount Allowed"
                        disc_type = "Debit"
                    elif voucher_type == "Purchase":
                        disc_ledger = "Discount Received"
                        disc_type = "Credit"
                    elif voucher_type == "Sales Return":
                        disc_ledger = "Discount Allowed"
                        disc_type = "Credit"
                    elif voucher_type == "Purchase Return":
                        disc_ledger = "Discount Allowed"
                        disc_type = "Debit"
                    else:
                        # Fallback
                        disc_ledger = "Discount"
                        disc_type = "Debit"
                        
                    voucher["ledger_entries"].append({
                        "ledger_name": disc_ledger,
                        "amount": discount_val,
                        "ledger_type": disc_type
                    })
            
            # Party Ledger Entry (Total)
            # Party Amount = Items + VAT - Discount
            # (Logic: Sales (Cr) + VAT (Cr) = Party (Dr) + Discount (Dr))
            # (Logic: Purchase (Dr) + VAT (Dr) = Party (Cr) + Discount (Cr))
            total_voucher_amount = round(total_item_amount + total_vat_amount - total_discount_amount, 2)
            voucher["ledger_entries"].append({
                "ledger_name": party_ledger_name,
                "amount": total_voucher_amount,
                "ledger_type": party_type
            })

        elif voucher_type == "Inventory Transfer":
             # Inventory Transfer
             # Requires From Location and To Location
             
             for row in group_list:
                 qty = float(row.get("Quantity", 0) or 0)
                 item_name = row.get("Item Name")
                 from_loc = row.get("From Location")
                 to_loc = row.get("To Location")
                 
                 if not from_loc or not to_loc:
                     raise ValueError(f"Both 'From Location' and 'To Location' are required for Inventory Transfer (Item: {item_name})")
                 
                 # Source (Credit)
                 voucher["item_entries"].append({
                     "item_name": item_name,
                     "quantity": qty,
                     "unit_price": 0, # Will be calc by add_voucher
                     "item_amount": 0,
                     "item_ledger_name": "Inventory",
                     "item_type": "Credit",
                     "location_name": from_loc
                 })
                 
                 # Dest (Debit)
                 voucher["item_entries"].append({
                     "item_name": item_name,
                     "quantity": qty,
                     "unit_price": 0, 
                     "item_amount": 0,
                     "item_ledger_name": "Inventory",
                     "item_type": "Debit",
                     "location_name": to_loc
                 })

        elif voucher_type == "Stock Adjustment":
             # Stock Adjustment logic matching Manual Screen:
             # Excel Columns: Ledger Name, Type (Debit/Credit), Item Name, Quantity.
             # User "Type" = Debit -> Expense (Stock Out).
             # User "Type" = Credit -> Income (Stock In).
             # Rate/Amount is auto-calculated during import/upload based on WAP.
             
             for row in group_list:
                 qty = abs(float(row.get("Quantity", 0) or 0))
                 # rate and amount are 0 initially, calculated at upload
                 rate = 0.0
                 amount = 0.0
                 
                 chosen_type = row.get("Type", "Debit") # Default to Debit if missing
                 ledger_name = row.get("Ledger Name")
                 
                 # Inventory Side (Opposite of chosen type)
                 # If User chose Debit (Expense), Stock must Credit (Decrease)
                 item_type = "Credit" if chosen_type == "Debit" else "Debit"
                 
                 voucher["item_entries"].append({
                     "item_name": row.get("Item Name"),
                     "quantity": qty,
                     "unit_price": rate,
                     "item_amount": amount,
                     "item_ledger_name": "Inventory",
                     "item_type": item_type,
                     "location_name": row.get("Location") or voucher.get("location_name")
                 })
                 
                 # Ledger Side (User Choice)
                 if ledger_name:
                     voucher["ledger_entries"].append({
                         "ledger_name": ledger_name,
                         "amount": amount,
                         "ledger_type": chosen_type,
                         "cost_center": row.get("Cost Center", "")
                     })
                 
        else:
            # Accounting Vouchers (Receipt, Payment, Contra, Journal, Expense, Service Income)
            
            total_output_vat = 0.0
            total_input_vat = 0.0
            
            for row in group_list:
                amount = round(float(row.get("Amount", 0) or 0), 2)
                l_type = row.get("Type")
                
                voucher["ledger_entries"].append({
                    "ledger_name": row.get("Ledger Name"),
                    "amount": amount,
                    "ledger_type": l_type, # Debit/Credit
                    "cost_center": row.get("Cost Center", "")
                })
                
                # VAT Logic for Journal, Expense, Service Income
                if voucher_type in ["Journal", "Expense", "Service Income"]:
                    vat_percent = float(row.get("VAT %", 0) or 0)
                    
                    if vat_percent > 0:
                        # Calculate VAT Amount automatically
                        # Formula: Exclusive -> Amount * Rate / 100
                        vat_amount = round(amount * (vat_percent / 100), 2)
                        
                        if vat_amount > 0:
                            # Determine Input vs Output
                            # Service Income: VAT on Credit -> Output VAT (Credit)
                            # Expense: VAT on Debit -> Input VAT (Debit)
                            # Journal: Debit -> Input, Credit -> Output
                            
                            if voucher_type == "Service Income" and l_type == "Credit":
                                total_output_vat += vat_amount
                            elif voucher_type == "Expense" and l_type == "Debit":
                                total_input_vat += vat_amount
                            elif voucher_type == "Journal":
                                if l_type == "Debit":
                                    total_input_vat += vat_amount
                                elif l_type == "Credit":
                                    total_output_vat += vat_amount
            
            # Append VAT Ledgers if any
            if total_output_vat > 0:
                voucher["ledger_entries"].append({
                    "ledger_name": "Output VAT 5%",
                    "amount": round(total_output_vat, 2),
                    "ledger_type": "Credit"
                })
            
            if total_input_vat > 0:
                voucher["ledger_entries"].append({
                    "ledger_name": "Input VAT 5%",
                    "amount": round(total_input_vat, 2),
                    "ledger_type": "Debit"
                })
        
        grouped_vouchers.append(voucher)
        
    return grouped_vouchers
