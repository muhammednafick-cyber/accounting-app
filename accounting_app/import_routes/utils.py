from accounting_app.models import validate_voucher_ledger_groups


def validate_import_data(import_type, data, company_id=None):
    print(f"Validating import_type={import_type}, data={data}, type={type(data)}, company_id={company_id}")

    if import_type in ["Group", "Ledger", "Cost Center", "Inventory Group", "Inventory", "Additional Charge"]:
        if not isinstance(data, list):
            data = [data]

        if import_type == "Group":
            for entry in data:
                if not all(key in entry for key in ["group_name", "nature"]) \
                   or entry["nature"] not in ["Assets", "Liabilities", "Income", "Expenses"]:
                    msg = f"Invalid group entry: {entry}"
                    print(msg)
                    return False, msg

        elif import_type == "Ledger":
            for entry in data:
                if not all(key in entry for key in ["ledger_code", "ledger_name", "group_name", "opening_balance", "opening_balance_type"]) \
                   or not isinstance(entry["opening_balance"], (int, float)) \
                   or entry["opening_balance_type"] not in ["Debit", "Credit"]:
                    msg = f"Invalid ledger entry: {entry}"
                    print(msg)
                    return False, msg

        elif import_type == "Cost Center":
            for entry in data:
                if not all(key in entry for key in ["center_code", "center_name"]):
                    msg = f"Invalid cost center entry: {entry}"
                    print(msg)
                    return False, msg

        elif import_type == "Inventory Group":
            for entry in data:
                if not all(key in entry for key in ["group_code", "group_name"]):
                    msg = f"Invalid inventory group entry: {entry}"
                    print(msg)
                    return False, msg

        elif import_type == "Sub Group":
            for entry in data:
                if not all(key in entry for key in ["Sub Group Name", "Parent Group Name"]):
                    # Allow "Parent Group" as alternate key
                    if not (entry.get("Sub Group Name") and (entry.get("Parent Group Name") or entry.get("Parent Group"))):
                        msg = f"Invalid sub group entry: {entry}"
                        print(msg)
                        return False, msg

        # ============ INVENTORY - 100% FIXED FOR YOUR EXCEL FILE ============
        elif import_type == "Inventory":
            for entry in data:
                # Required base columns
                base_required = ["Item Code", "Item Name", "Group Name", "Unit"]
                if not all(key in entry for key in base_required):
                    msg = f"Missing required column in entry: {entry}"
                    print(msg)
                    return False, msg

                # Selling Price (optional) - accept 'Selling Price' or 'Unit Price'
                price_val = entry.get("Selling Price", entry.get("Unit Price", 0))
                try:
                    _price = float(price_val or 0)
                except (ValueError, TypeError):
                    msg = f"Selling/Unit Price not a number: {price_val}"
                    print(msg)
                    return False, msg

                # VAT Rate - optional, default 0
                vat_rate = entry.get("VAT 5%", 0)
                try:
                    vat_rate = float(vat_rate)
                except (ValueError, TypeError):
                    vat_rate = 0.0

                # Opening fields — if Opening Quantity > 0, Purchase Date and Balancing Ledger are mandatory
                opening_qty = entry.get("Opening Quantity", "")
                opening_price = entry.get("Opening Price (Cost)", "")
                try:
                    _oq = float(opening_qty) if opening_qty not in (None, "") else 0.0
                except (ValueError, TypeError):
                    msg = f"Opening Quantity not a number: {opening_qty}"
                    print(msg)
                    return False, msg
                if opening_price not in (None, ""):
                    try:
                        _op = float(opening_price)
                    except (ValueError, TypeError):
                        msg = f"Opening Price (Cost) not a number: {opening_price}"
                        print(msg)
                        return False, msg
                if _oq > 0:
                    purchase_date = str(entry.get("Purchase Date", "") or "").strip()
                    if not purchase_date:
                        msg = f"Purchase Date is required for item '{entry.get('Item Name')}' when Opening Quantity > 0"
                        print(msg)
                        return False, msg
                    balancing_ledger = str(entry.get("Balancing Ledger", "") or "").strip()
                    if not balancing_ledger:
                        msg = f"Balancing Ledger is required for item '{entry.get('Item Name')}' when Opening Quantity > 0"
                        print(msg)
                        return False, msg
        
        elif import_type == "Additional Charge":
            # data here is list of vouchers (dicts) from _group_voucher_rows
            # Each dict has: date, linked_voucher_number, charges (list)
            if not isinstance(data, list):
                data = [data]
                
            for entry in data:
                required = ["date", "linked_voucher_number", "charges"]
                if not all(k in entry for k in required):
                    return False, f"Missing fields in Additional Charge voucher: {entry}"
                
                if not entry["linked_voucher_number"]:
                    return False, f"Linked Purchase Voucher is required for Additional Charge. Date: {entry.get('date')}"

                if not isinstance(entry["charges"], list) or not entry["charges"]:
                    return False, f"No charges found for Additional Charge voucher. Date: {entry.get('date')}"
                
                for charge in entry["charges"]:
                    if not all(k in charge for k in ["amount", "valuation_method", "party_ledger"]):
                        return False, f"Invalid charge entry (missing Amount, Method or Party Ledger): {charge}"
                    try:
                        float(charge["amount"])
                    except:
                        return False, f"Invalid charge amount: {charge['amount']}"

        print(f"Validation succeeded for {import_type}")
        return True, "Success"

    # ============ VOUCHER VALIDATION ============
    else:
        if isinstance(data, list):
            for idx, voucher in enumerate(data):
                is_valid, err = validate_single_voucher(import_type, voucher, company_id=company_id)
                if not is_valid:
                    return False, f"Voucher {idx+1}: {err}"
            print(f"Validation succeeded for {import_type} (List of {len(data)} vouchers)")
            return True, "Success"
        else:
            is_valid, err = validate_single_voucher(import_type, data, company_id=company_id)
            if is_valid:
                print(f"Validation succeeded for {import_type}: {data}")
                return True, "Success"
            return False, err

def validate_single_voucher(import_type, data, company_id=None):
    from datetime import datetime
    if not isinstance(data, dict):
        msg = f"Invalid voucher data: Expected dict, got {type(data)} - {data}"
        print(msg)
        return False, msg

    date_val = data.get("date")
    # Modified check: Allow empty ledger_entries if item_entries exist (for Inventory Transfer)
    has_ledgers = isinstance(data.get("ledger_entries"), list) and bool(data["ledger_entries"])
    has_items = isinstance(data.get("item_entries"), list) and bool(data["item_entries"])
    
    if not date_val:
        msg = f"Invalid voucher data: Missing date - {data}"
        print(msg)
        return False, msg
        
    if not has_ledgers and not has_items:
        msg = f"Invalid voucher data: Must have at least one ledger entry or item entry - {data}"
        print(msg)
        return False, msg

    # Validate Date Format (YYYY-MM-DD)
    from accounting_app.models import parse_date
    normalized_date = parse_date(str(date_val).strip())
    try:
        datetime.strptime(normalized_date, "%Y-%m-%d")
        # Update the data object with normalized date so downstream uses YYYY-MM-DD
        data["date"] = normalized_date
    except ValueError:
        msg = f"Invalid date format '{date_val}'. Expected YYYY-MM-DD or DD-MM-YYYY."
        print(msg)
        return False, msg

    # Cost Center Mandatory Check
    from database import get_company_settings
    
    COST_CENTER_ALLOWED_TYPES_IMPORT = {
        "Journal",
        "Expense",
        "Sales",
        "Sales Return",
        "Service Income",
        "Purchase",
        "Purchase Return",
        "Stock Adjustment",
        "Payment",
        "Receipt",
        "Contra",
    }
    
    company = get_company_settings(company_id=company_id)
    if company and company.get("cost_center_applicable") and company.get("cost_center_mandatory") and import_type in COST_CENTER_ALLOWED_TYPES_IMPORT:
        header_cc = data.get("cost_center")
        # If Header CC is present, we assume it defaults to all lines -> Valid.
        # If Header CC is missing, ALL relevant lines must have CC.
        if not header_cc:
            missing_cc = False
            # For Inventory Vouchers (Sales, Purchase, etc.), check item_entries
            # Note: We check existence of 'item_entries' list.
            if has_items:
                for ie in data.get("item_entries", []):
                    if not ie.get("cost_center"):
                        missing_cc = True
                        break
            # For Accounting Vouchers (Expense, Journal, etc.), check ledger_entries
            elif has_ledgers:
                for le in data.get("ledger_entries", []):
                    if not le.get("cost_center"):
                        missing_cc = True
                        break
            
            if missing_cc:
                msg = "Cost Center is mandatory. Please specify a Cost Center in the Header or for every Entry."
                print(msg)
                return False, msg

    for entry in data["ledger_entries"]:
        if not all(key in entry for key in ["ledger_name", "amount", "ledger_type"]) \
            or not isinstance(entry["amount"], (int, float)) \
            or entry["ledger_type"] not in ["Debit", "Credit"]:
            msg = f"Invalid ledger entry: {entry}"
            print(msg)
            return False, msg

    ledger_entries_for_rule = []
    for entry in data["ledger_entries"]:
        ledger_entries_for_rule.append({
            "ledger_name": entry["ledger_name"],
            "amount": entry["amount"],
            "type": entry["ledger_type"],
        })

    is_valid, err = validate_voucher_ledger_groups(import_type, ledger_entries_for_rule, company_id=company_id)
    if not is_valid:
        msg = f"Voucher group validation failed: {err}"
        print(f"Voucher group validation failed for import_type={import_type}: {err}")
        return False, msg

    if "item_entries" in data:
        for entry in data.get("item_entries", []):
            if not all(key in entry for key in ["item_name", "quantity", "unit_price", "item_amount", "item_ledger_name", "item_type"]) \
                or not isinstance(entry["quantity"], (int, float)) \
                or not isinstance(entry["unit_price"], (int, float)) \
                or not isinstance(entry["item_amount"], (int, float)) \
                or entry["item_type"] not in ["Debit", "Credit"]:
                msg = f"Invalid item entry: {entry}"
                print(msg)
                return False, msg

            # Check for negative quantity in Sales and Purchase
            if import_type in ["Sales", "Purchase"] and entry["quantity"] < 0:
                msg = f"Invalid quantity for {import_type}: {entry['quantity']}. Quantity cannot be less than 0."
                print(msg)
                return False, msg
    return True, None


def insert_import_queue(cursor, company_id, date, file_name, voucher_type, json_data, validation_status, upload_status, missing_ledger=None, failure_reason=None):
    """
    Helper function to insert into import_queue with cross-database compatibility.
    Uses generic execute_insert_returning_id from config.
    """
    from database.config import execute_insert_returning_id
    
    sql = """
        INSERT INTO import_queue (
            company_id, date, file_name, voucher_type, json_data,
            validation_status, upload_status,
            missing_ledger, failure_reason
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    
    params = (
        company_id,
        date,
        file_name,
        voucher_type,
        json_data,
        validation_status,
        upload_status,
        missing_ledger,
        failure_reason,
    )

    return execute_insert_returning_id(cursor, sql, params)