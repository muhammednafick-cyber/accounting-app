"""
Vouchers module: Voucher creation and entries
"""
# import sqlite3 - removed
from .config import get_connection
from .reports_db import calculate_weighted_average_price, calculate_weighted_average_price_at, replay_movements_by_location, get_trial_balance_data
from datetime import datetime
from .financial_year_db import get_fy_by_date, get_fy_by_id
from .company_db import get_current_company_id

# _initialize_voucher_tables removed - handled by unified_db

def validate_return_quantity(ref_voucher_number, item_name, return_qty, current_voucher_number=None, company_id=None):
    """
    Validates that the return quantity does not exceed the original quantity 
    minus already returned quantities.
    Returns (bool, message).
    """
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return False, "Company ID is required"

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # 1. Get original quantity from the referenced voucher
        cursor.execute(
            "SELECT quantity FROM item_entries WHERE company_id = %s AND voucher_number = %s AND item_name = %s",
            (company_id, ref_voucher_number, item_name)
        )
        row = cursor.fetchone()
        if not row:
            return False, f"Original item '{item_name}' not found in voucher {ref_voucher_number}"
        
        # cursor.fetchone() returns a tuple or RealDictRow
        original_qty = abs(float(row[0] if not hasattr(row, 'keys') else row['quantity'] or 0))

        # 2. Get total quantity already returned (excluding current voucher if editing)
        query = """
            SELECT SUM(quantity) FROM item_entries 
            WHERE company_id = %s AND ref_voucher_number = %s AND item_name = %s
        """
        params = [company_id, ref_voucher_number, item_name]

        if current_voucher_number:
            query += " AND voucher_number != %s"
            params.append(current_voucher_number)

        cursor.execute(query, tuple(params))
        result = cursor.fetchone()
        
        if result:
             if isinstance(result, dict) or hasattr(result, 'keys'):
                  # RealDictCursor keys depend on query. SUM(quantity) -> 'sum'
                  returned_so_far = abs(float(result['sum'] or 0))
             else:
                  returned_so_far = abs(float(result[0] or 0))
        else:
             returned_so_far = 0.0

        total_after_this_return = returned_so_far + abs(float(return_qty))

        if total_after_this_return > original_qty:
            return False, f"Total return quantity ({total_after_this_return}) exceeds original quantity ({original_qty}) for item '{item_name}' in voucher {ref_voucher_number}"
        
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()

def add_additional_charges_voucher(date, linked_voucher_number, charges, narration='', db_connection=None, skip_recalc=False, party_ledger=None, company_id=None):
    """
    Create an Additional Charges Voucher and allocate costs to the linked Purchase Voucher items.
    charges: list of dicts { 'amount': float, 'valuation_method': str, 'narration': str, 'party_ledger': str }
    """
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

    if db_connection:
        conn = db_connection
        should_close = False
    else:
        conn = get_connection()
        should_close = True

    cursor = conn.cursor()
    try:
        # Override date to match linked purchase voucher date for correct accounting period
        # This ensures that the financial impact (GL entries) of the additional charges
        # is recorded in the same period as the original purchase, aligning Balance Sheet with Inventory Valuation.
        if linked_voucher_number:
            cursor.execute("SELECT date FROM vouchers WHERE company_id = %s AND voucher_number = %s", (company_id, linked_voucher_number))
            linked_res = cursor.fetchone()
            if linked_res:
                date = linked_res['date'] if hasattr(linked_res, 'keys') else linked_res[0]

        # 1. Generate Voucher Number
        voucher_type = "Additional Charge"
        prefix = "ADD"
        cursor.execute("SELECT COUNT(*) as count FROM vouchers WHERE company_id = %s AND voucher_type = %s", (company_id, voucher_type))
        count_res = cursor.fetchone()
        if count_res:
             count = count_res['count'] if isinstance(count_res, dict) or hasattr(count_res, 'keys') else count_res[0]
        else:
             count = 0
        voucher_number = f"{prefix}-{str(count + 1).zfill(5)}"
        
        cursor.execute("SELECT COALESCE(MAX(voucher_id),0)+1 as next_id FROM vouchers WHERE company_id = %s", (company_id,))
        next_vid_res = cursor.fetchone()
        if next_vid_res:
             next_vid = (next_vid_res['next_id'] if isinstance(next_vid_res, dict) or hasattr(next_vid_res, 'keys') else next_vid_res[0]) or 1
        else:
             next_vid = 1
        
        posting_date = datetime.today().strftime('%Y-%m-%d')
        entry_date = datetime.today().strftime('%Y-%m-%d %H:%M:%S')
        
        total_charges_amount = sum(float(c['amount']) for c in charges)
        total_vat_amount = sum(float(c.get('vat_amount', 0)) for c in charges)
        total_voucher_amount = total_charges_amount + total_vat_amount
        
        # 2. Insert Voucher Header
        cursor.execute("""
            INSERT INTO vouchers (company_id, voucher_number, voucher_type, date, posting_date, amount, narration, entry_date, voucher_id, linked_voucher_number)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (company_id, voucher_number, voucher_type, date, posting_date, total_voucher_amount, narration, entry_date, next_vid, linked_voucher_number))
        
        # 3. Fetch Linked Purchase Invoice Items
        cursor.execute("""
            SELECT id, item_name, quantity, unit_price, weight_kg, landed_cost_per_unit, total_additional_charges_allocated 
            FROM item_entries 
            WHERE company_id = %s AND voucher_number = %s
        """, (company_id, linked_voucher_number))
        
        items = []
        rows = cursor.fetchall()
        if not rows:
            raise ValueError(f"Linked voucher {linked_voucher_number} has no items or does not exist.")
            
        for row in rows:
            # Handle DictCursor or tuple
            if isinstance(row, dict) or hasattr(row, 'keys'):
                r_id = row['id']
                r_name = row['item_name']
                r_qty = row['quantity']
                r_price = row['unit_price']
                r_weight = row['weight_kg']
                r_landed = row['landed_cost_per_unit']
                r_alloc = row['total_additional_charges_allocated']
            else:
                r_id = row[0]
                r_name = row[1]
                r_qty = row[2]
                r_price = row[3]
                r_weight = row[4]
                r_landed = row[5]
                r_alloc = row[6]

            items.append({
                'id': r_id,
                'item_name': r_name,
                'quantity': float(r_qty or 0),
                'unit_price': float(r_price or 0),
                'weight_kg': float(r_weight) if r_weight is not None else 0.0,
                'landed_cost_per_unit': float(r_landed) if r_landed is not None else float(r_price or 0),
                'total_additional_charges_allocated': float(r_alloc or 0)
            })
            
        # Accumulators for updates per item
        item_allocations = {item['id']: 0.0 for item in items}
        
        # 4. Process Charges
        for charge in charges:
            amount = float(charge['amount'])
            method = charge['valuation_method']
            c_narration = charge.get('narration', '')
            c_party = charge.get('party_ledger', '')
            c_vat = float(charge.get('vat_amount', 0))
            
            # Insert Charge Line
            cursor.execute("""
                INSERT INTO additional_charge_entries (company_id, voucher_number, amount, valuation_method, narration, party_ledger, vat_amount)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (company_id, voucher_number, amount, method, c_narration, c_party, c_vat))
            
            # Allocation Logic
            if method == 'Quantity':
                total_qty = sum(item['quantity'] for item in items)
                if total_qty == 0:
                    pass
                
                if total_qty > 0:
                    for item in items:
                        share = (item['quantity'] / total_qty) * amount
                        item_allocations[item['id']] += share
                    
            elif method == 'Value':
                total_val = sum(item['quantity'] * item['unit_price'] for item in items)
                if total_val > 0:
                    for item in items:
                        item_val = item['quantity'] * item['unit_price']
                        share = (item_val / total_val) * amount
                        item_allocations[item['id']] += share
                    
            elif method == 'Weight (KG)':
                total_weight = sum(item['weight_kg'] for item in items)
                if total_weight > 0:
                    for item in items:
                        share = (item['weight_kg'] / total_weight) * amount
                        item_allocations[item['id']] += share
                else:
                     raise ValueError(f"Total weight is 0 for voucher {linked_voucher_number}, cannot allocate by Weight. Ensure items have weight.")

        # 5. Update Purchase Invoice Items
        for item in items:
            allocated_amt = item_allocations[item['id']]
            if allocated_amt == 0:
                continue
                
            new_total_allocated = item['total_additional_charges_allocated'] + allocated_amt
            extra_per_unit = allocated_amt / item['quantity'] if item['quantity'] != 0 else 0
            new_landed_cost = item['landed_cost_per_unit'] + extra_per_unit
            
            cursor.execute("""
                UPDATE item_entries 
                SET landed_cost_per_unit = %s, total_additional_charges_allocated = %s
                WHERE id = %s AND company_id = %s
            """, (new_landed_cost, new_total_allocated, item['id'], company_id))
            
        # 6. Create GL Entries
        
        # Credit Parties (Grouped by Party Ledger to avoid duplicate entries per party)
        party_credits = {}
        for charge in charges:
            p_ledger = charge.get('party_ledger') or party_ledger
            if not p_ledger:
                 raise ValueError("Party Ledger is missing for one of the charges.")
            
            amt = float(charge['amount']) + float(charge.get('vat_amount', 0))
            party_credits[p_ledger] = party_credits.get(p_ledger, 0.0) + amt

        for p_ledger, p_amount in party_credits.items():
            if p_amount <= 0: continue
            
            # Verify Party Ledger Exists
            cursor.execute("SELECT COUNT(*) as count FROM ledgers WHERE company_id = %s AND ledger_name = %s", (company_id, p_ledger))
            res = cursor.fetchone()
            exists = 0
            if res:
                 exists = res['count'] if isinstance(res, dict) or hasattr(res, 'keys') else res[0]
            if exists == 0:
                raise ValueError(f"Party Ledger '{p_ledger}' does not exist. Please create it first.")

            # Credit Party
            cursor.execute("""
                INSERT INTO ledger_entries (company_id, voucher_number, ledger_name, amount, type)
                VALUES (%s, %s, %s, %s, 'Credit')
            """, (company_id, voucher_number, p_ledger, p_amount))
            
            cursor.execute("UPDATE ledgers SET closing_balance = closing_balance - %s WHERE company_id = %s AND ledger_name = %s", (p_amount, company_id, p_ledger))
        
        # Debit Inventory (using a single entry for simplicity as per requirement "Debit Inventory / Purchase Adjustment by the total")
        # Ensure Inventory ledger exists
        # Removed redundant broken check here
        
        # Re-check properly
        cursor.execute("SELECT 1 FROM ledgers WHERE company_id = %s AND ledger_name = %s", (company_id, "Inventory"))
        if not cursor.fetchone():
             cursor.execute("INSERT INTO ledgers (company_id, ledger_code, ledger_name, group_code, opening_balance, opening_balance_type, closing_balance) VALUES (%s, %s, %s, %s, %s, %s, %s)", (company_id, "LINV", "Inventory", "G010", 0, "Debit", 0))

        # Only debit Inventory with the Charges Amount (excluding VAT)
        if total_charges_amount > 0:
            cursor.execute("""
                INSERT INTO ledger_entries (company_id, voucher_number, ledger_name, amount, type)
                VALUES (%s, %s, %s, %s, 'Debit')
            """, (company_id, voucher_number, "Inventory", total_charges_amount))
            
            cursor.execute("UPDATE ledgers SET closing_balance = closing_balance + %s WHERE company_id = %s AND ledger_name = %s", (total_charges_amount, company_id, "Inventory"))

        # Debit Input VAT (if any)
        if total_vat_amount > 0:
            vat_ledger = "Input VAT 5%"
            
            cursor.execute("""
                INSERT INTO ledger_entries (company_id, voucher_number, ledger_name, amount, type)
                VALUES (%s, %s, %s, %s, 'Debit')
            """, (company_id, voucher_number, vat_ledger, total_vat_amount))
            
            cursor.execute("UPDATE ledgers SET closing_balance = closing_balance + %s WHERE company_id = %s AND ledger_name = %s", (total_vat_amount, company_id, vat_ledger))
        
        if should_close:
            conn.commit()
        
        # Trigger recalculation for affected items
        # We start from the linked purchase voucher's date because that's where the cost changed
        cursor.execute("SELECT date FROM vouchers WHERE company_id = %s AND voucher_number = %s", (company_id, linked_voucher_number))
        linked_date_row = cursor.fetchone()
        if linked_date_row:
             linked_date = linked_date_row['date'] if isinstance(linked_date_row, dict) or hasattr(linked_date_row, 'keys') else linked_date_row[0]
        else:
             linked_date = date

        if not skip_recalc:
            try:
                unique_items = set(item['item_name'] for item in items)
                for item_name in unique_items:
                    recalculate_running_balance_for_item(item_name, linked_date, company_id=company_id)
            except Exception as e:
                print(f"Error triggering running balance calc in additional charges: {e}")

        return voucher_number
        
    except Exception as e:
        if should_close:
            conn.rollback()
        raise e
    finally:
        cursor.close()
        if should_close:
            conn.close()

def get_current_stock(item_name, location_name, date=None, exclude_voucher_number=None, conn=None, company_id=None):
    """
    Get the current stock quantity for an item at a specific location using optimized SQL aggregation.
    """
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return 0.0

    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    cursor = conn.cursor()
    try:
        location_name = location_name or 'Main Location'

        # 1. Get Global Current Stock and Opening Location from inventory table
        cursor.execute(
            "SELECT stock_quantity, COALESCE(opening_location_name, 'Main Location') FROM inventory WHERE company_id = %s AND name = %s",
            (company_id, item_name)
        )
        row = cursor.fetchone()
        if not row:
            return 0.0
            
        # Handle dict or tuple
        if isinstance(row, dict) or hasattr(row, 'keys'):
             global_current_stock = float(row['stock_quantity'] or 0)
             opening_location = row['coalesce'] # keys are lowecase column names usually
             # Wait, COALESCE expression name depends on driver. RealDictCursor uses column alias.
             # Better to use index optimization or aliasing in query
        else:
             global_current_stock = float(row[0] or 0)
             opening_location = row[1]
             
        # Re-query with alias for safety if using DictCursor
        cursor.execute(
            "SELECT stock_quantity, COALESCE(opening_location_name, 'Main Location') as op_loc FROM inventory WHERE company_id = %s AND name = %s",
            (company_id, item_name)
        )
        row = cursor.fetchone()
        if isinstance(row, dict) or hasattr(row, 'keys'):
             global_current_stock = float(row['stock_quantity'] or 0)
             opening_location = row['op_loc']
        else:
             global_current_stock = float(row[0] or 0)
             opening_location = row[1]


        # 2. Calculate Total Global Movements (All time) to derive Baseline Opening Quantity
        # This reverses the global stock to find what the stock was before any transactions
        cursor.execute(
            """
            SELECT 
                SUM(CASE 
                    WHEN v.voucher_type IN ('Purchase','Sales Return','Physical Stock') THEN ie.quantity
                    WHEN v.voucher_type = 'Inventory Transfer' AND ie.type = 'Debit' THEN ie.quantity
                    WHEN v.voucher_type = 'Stock Adjustment' AND ie.type = 'Debit' THEN ie.quantity
                    ELSE 0 END) as in_qty,
                SUM(CASE 
                    WHEN v.voucher_type IN ('Sales','Purchase Return') THEN ie.quantity
                    WHEN v.voucher_type = 'Inventory Transfer' AND ie.type = 'Credit' THEN ie.quantity
                    WHEN v.voucher_type = 'Stock Adjustment' AND ie.type = 'Credit' THEN ie.quantity
                    ELSE 0 END) as out_qty
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE ie.company_id = %s AND v.company_id = %s AND ie.item_name = %s
            """,
            (company_id, company_id, item_name)
        )
        row_global = cursor.fetchone()
        # Handle None
        if row_global:
             if isinstance(row_global, dict) or hasattr(row_global, 'keys'):
                  r0 = row_global['in_qty']
                  r1 = row_global['out_qty']
             else:
                  r0 = row_global[0]
                  r1 = row_global[1]
        else:
             r0, r1 = 0, 0
        global_in = float(r0 or 0)
        global_out = float(r1 or 0)
        global_net_movements = global_in - global_out

        # Baseline Opening = Current - Net Movements
        baseline_opening_qty = global_current_stock - global_net_movements
        if baseline_opening_qty < 0:
            baseline_opening_qty = 0.0

        # 3. Calculate Net Movements for the Specific Location (Filtered by Date and Exclusion)
        query_loc = """
            SELECT 
                SUM(CASE 
                    WHEN v.voucher_type IN ('Purchase','Sales Return','Physical Stock') THEN ie.quantity
                    WHEN v.voucher_type = 'Inventory Transfer' AND ie.type = 'Debit' THEN ie.quantity
                    WHEN v.voucher_type = 'Stock Adjustment' AND ie.type = 'Debit' THEN ie.quantity
                    ELSE 0 END) as in_qty,
                SUM(CASE 
                    WHEN v.voucher_type IN ('Sales','Purchase Return') THEN ie.quantity
                    WHEN v.voucher_type = 'Inventory Transfer' AND ie.type = 'Credit' THEN ie.quantity
                    WHEN v.voucher_type = 'Stock Adjustment' AND ie.type = 'Credit' THEN ie.quantity
                    ELSE 0 END) as out_qty
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE ie.company_id = %s AND v.company_id = %s AND ie.item_name = %s 
              AND COALESCE(ie.location_name, 'Main Location') = %s
        """
        params_loc = [company_id, company_id, item_name, location_name]

        if date:
            query_loc += " AND v.date <= %s"
            params_loc.append(date)
            
        if exclude_voucher_number:
            query_loc += " AND v.voucher_number != %s"
            params_loc.append(exclude_voucher_number)

        cursor.execute(query_loc, tuple(params_loc))
        row_loc = cursor.fetchone()
        if row_loc:
             if isinstance(row_loc, dict) or hasattr(row_loc, 'keys'):
                  r0 = row_loc['in_qty']
                  r1 = row_loc['out_qty']
             else:
                  r0 = row_loc[0]
                  r1 = row_loc[1]
        else:
             r0, r1 = 0, 0
        loc_in = float(r0 or 0)
        loc_out = float(r1 or 0)
        loc_net_movements = loc_in - loc_out

        # 4. Final Calculation
        current_loc_stock = loc_net_movements
        if location_name == opening_location:
            current_loc_stock += baseline_opening_qty

        return current_loc_stock

    except Exception as e:
        print(f"Error in get_current_stock: {e}")
        return 0.0
    finally:
        if should_close:
            conn.close()


def _compute_cogs_for_entry(voucher_type, date, entry, cursor, voucher_id_cutoff=None, company_id=None):
    try:
        item_name = entry.get('item_name')
        qty = abs(float(entry.get('quantity') or 0))
        loc_override = entry.get('_location_override') or entry.get('location_name')

        # Always use Global WAP as per user requirement
        if voucher_id_cutoff is not None:
            wap_rate = float(calculate_weighted_average_price_at(item_name, date, voucher_id_cutoff, company_id=company_id) or 0)
        else:
            wap_rate = float(calculate_weighted_average_price(item_name, date, company_id=company_id) or 0)

        cogs_rate = wap_rate
        cogs_amount = round(qty * cogs_rate, 2)

        if voucher_type == 'Sales Return':
            pass
        elif voucher_type == 'Sales':
            pass
        else:
            cogs_rate, cogs_amount = None, None

        return cogs_rate, cogs_amount
    except Exception:
        return None, None

def ensure_item_entries_cogs_populated(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Check columns using information_schema
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'item_entries'
        """)
        cols_ie = [row[0] if not isinstance(row, dict) else row['column_name'] for row in cursor.fetchall()]
        
        if 'cogs_rate' not in cols_ie:
            cursor.execute("ALTER TABLE item_entries ADD COLUMN cogs_rate REAL")
        if 'cogs_amount' not in cols_ie:
            cursor.execute("ALTER TABLE item_entries ADD COLUMN cogs_amount REAL")
        if 'ref_voucher_number' not in cols_ie:
            cursor.execute("ALTER TABLE item_entries ADD COLUMN ref_voucher_number TEXT")
        
        conn.commit()

        cursor.execute(
            """
            SELECT ie.id, ie.item_name, ie.quantity, v.date, v.voucher_type, ie.ref_voucher_number
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE ie.company_id = %s AND v.company_id = %s
              AND v.voucher_type IN ('Sales','Sales Return')
              AND (ie.cogs_amount IS NULL OR ie.cogs_rate IS NULL)
            """, (company_id, company_id)
        )
        rows = cursor.fetchall()
        for row in rows:
            if isinstance(row, dict) or hasattr(row, 'keys'):
                id_ = row['id']
                item_name = row['item_name']
                qty = row['quantity']
                date = row['date']
                vtype = row['voucher_type']
                ref_no = row['ref_voucher_number']
            else:
                id_ = row[0]
                item_name = row[1]
                qty = row[2]
                date = row[3]
                vtype = row[4]
                ref_no = row[5]

            entry = {'item_name': item_name, 'quantity': qty, '_ref_voucher_number': ref_no}
            cogs_rate, cogs_amount = _compute_cogs_for_entry(vtype, date, entry, cursor, company_id=company_id)
            cursor.execute(
                "UPDATE item_entries SET cogs_rate = %s, cogs_amount = %s WHERE id = %s",
                (cogs_rate, cogs_amount, id_)
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise Exception(str(e))
    finally:
        conn.close()

def add_voucher(voucher_type, date, ledger_entries, item_entries,
                cost_center_code=None, narration='', location_name=None, credit_days=None, due_date=None, 
                original_invoice_date=None, original_invoice_ref=None, linked_voucher_number=None,
                skip_recalc=False, db_connection=None, company_id=None, allow_locked_fy=False):
    
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

    # Financial Year Validation
    fy = get_fy_by_date(date, company_id=company_id)
    if not fy:
        # If no FY found, strictly enforce creation
        raise Exception(f"No Financial Year defined for date {date}. Please create a Financial Year first.")
    
    if fy['is_locked'] and not allow_locked_fy:
        raise Exception(f"Financial Year {fy['fy_code']} is locked. Cannot add voucher.")

    if db_connection:
        conn = db_connection
        should_close = False
    else:
        conn = get_connection()
        should_close = True
        
    cursor = conn.cursor()
    try:
        posting_date = datetime.today().strftime('%Y-%m-%d')
        entry_date = datetime.today().strftime('%Y-%m-%d %H:%M:%S')
        
        def _ensure_core_ledgers():
            cursor.execute("SELECT COUNT(*) FROM ledgers WHERE company_id = %s AND ledger_name = %s", (company_id, "Inventory"))
            exists_inv = cursor.fetchone()[0] or 0
            if exists_inv == 0:
                cursor.execute(
                    "INSERT INTO ledgers (company_id, ledger_code, ledger_name, group_code, opening_balance, opening_balance_type, closing_balance) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (company_id, "LINV", "Inventory", "G010", 0, "Debit", 0)
                )
            cursor.execute("SELECT COUNT(*) FROM ledgers WHERE company_id = %s AND ledger_name = %s", (company_id, "Cost of Goods Sold"))
            exists_cogs = cursor.fetchone()[0] or 0
            if exists_cogs == 0:
                cursor.execute(
                    "INSERT INTO ledgers (company_id, ledger_code, ledger_name, group_code, opening_balance, opening_balance_type, closing_balance) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (company_id, "LCOGS", "Cost of Goods Sold", "G012", 0, "Debit", 0)
                )

            # Ensure VAT Ledgers (Input/Output)
            cursor.execute("SELECT COUNT(*) FROM ledgers WHERE company_id = %s AND ledger_name = %s", (company_id, "Input VAT 5%"))
            exists_vat_in = cursor.fetchone()[0] or 0
            if exists_vat_in == 0:
                cursor.execute(
                    "INSERT INTO ledgers (company_id, ledger_code, ledger_name, group_code, opening_balance, opening_balance_type, closing_balance) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (company_id, "LVATIN", "Input VAT 5%", "G011", 0, "Debit", 0)
                )
                
            cursor.execute("SELECT COUNT(*) FROM ledgers WHERE company_id = %s AND ledger_name = %s", (company_id, "Output VAT 5%"))
            exists_vat_out = cursor.fetchone()[0] or 0
            if exists_vat_out == 0:
                cursor.execute(
                    "INSERT INTO ledgers (company_id, ledger_code, ledger_name, group_code, opening_balance, opening_balance_type, closing_balance) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (company_id, "LVATOUT", "Output VAT 5%", "G011", 0, "Credit", 0)
                )

        if voucher_type == 'Inventory Transfer':
            ledger_entries = []
            adjusted_items = []
            source_entry = None
            for ie in item_entries:
                if ie.get('type') == 'Credit':
                    source_entry = ie
                    break
            if source_entry:
                src_item = source_entry['item_name']
                # Use Global WAP for transfer pricing
                src_wap = float(calculate_weighted_average_price(src_item, date, company_id=company_id) or 0)
                
                for ie in item_entries:
                    qty_mag = abs(float(ie['quantity'] or 0))
                    ie['unit_price'] = src_wap
                    ie['amount'] = round(qty_mag * src_wap, 2)
                    adjusted_items.append(ie)
                item_entries = adjusted_items
        if voucher_type == 'Stock Adjustment':
            _ensure_core_ledgers()
            adjusted_items = []
            for entry in item_entries:
                qty_mag = abs(float(entry['quantity'] or 0))
                wap = calculate_weighted_average_price(entry['item_name'], date, company_id=company_id)
                unit_rate = float(wap or 0)
                entry['unit_price'] = unit_rate
                entry['amount'] = round(qty_mag * unit_rate, 2)
                adjusted_items.append(entry)
            item_entries = adjusted_items

            # Align GL amounts to match item entries totals for TB consistency
            debit_item_total = sum(e['amount'] for e in item_entries if e['type'] == 'Debit')
            credit_item_total = sum(e['amount'] for e in item_entries if e['type'] == 'Credit')
            
            has_inventory_dr = False
            has_inventory_cr = False
            
            for le in ledger_entries:
                if le['ledger_name'] == 'Inventory':
                    if le['type'] == 'Debit':
                        le['amount'] = debit_item_total
                        has_inventory_dr = True
                    else:
                        le['amount'] = credit_item_total
                        has_inventory_cr = True
                elif le['type'] == 'Debit':
                    le['amount'] = credit_item_total
                elif le['type'] == 'Credit':
                    le['amount'] = debit_item_total
            
            if debit_item_total > 0 and not has_inventory_dr:
                 ledger_entries.append({'ledger_name': 'Inventory', 'amount': debit_item_total, 'type': 'Debit'})
            
            if credit_item_total > 0 and not has_inventory_cr:
                 ledger_entries.append({'ledger_name': 'Inventory', 'amount': credit_item_total, 'type': 'Credit'})
            
        if voucher_type == 'Opening':
            adjusted_items = []
            total_items_amount = 0.0
            cursor.execute("SELECT COUNT(*) FROM ledgers WHERE company_id = %s AND ledger_name = %s", (company_id, "Inventory"))
            if (cursor.fetchone()[0] or 0) == 0:
                cursor.execute(
                    "INSERT INTO ledgers (company_id, ledger_code, ledger_name, group_code, opening_balance, opening_balance_type, closing_balance) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (company_id, "LINV", "Inventory", "G010", 0, "Debit", 0)
                )
            for entry in item_entries:
                qty_mag = abs(float(entry['quantity'] or 0))
                unit_rate = float(entry['unit_price'] or 0)
                amount_line = round(qty_mag * unit_rate, 2)
                entry['amount'] = amount_line
                entry['type'] = 'Debit'
                
                entry['running_qty'] = qty_mag
                entry['running_value'] = amount_line
                entry['running_wap'] = unit_rate
                
                adjusted_items.append(entry)
                total_items_amount += amount_line
                cursor.execute("UPDATE inventory SET opening_price = %s, opening_location_name = %s WHERE company_id = %s AND name = %s", (unit_rate, (location_name or 'Main Location'), company_id, entry['item_name']))
            item_entries = adjusted_items
            if total_items_amount > 0:
                ledger_entries.append({'ledger_name': 'Inventory', 'amount': total_items_amount, 'type': 'Debit'})

        if voucher_type in ('Purchase','Purchase Return'):
            _ensure_core_ledgers()
            adjusted_items = []
            item_ledgers = set()
            total_item_amount = 0.0
            
            for entry in item_entries:
                qty_mag = abs(float(entry.get('quantity') or 0))
                unit_rate = float(entry.get('unit_price') or 0)
                amount_line = round(qty_mag * unit_rate, 2)
                e = dict(entry)
                e['amount'] = amount_line
                # FORCE LEDGER NAME TO 'Inventory' - This satisfies FK constraints and is semantically correct
                # The backend logic aggregates these into a single 'Inventory' GL entry, but row-level data needs a valid ledger.
                e['ledger_name'] = 'Inventory' 
                
                adjusted_items.append(e)
                total_item_amount += amount_line

            item_entries = adjusted_items
            
            # Filter out Inventory ledger if passed from frontend (to avoid duplication)
            # We will re-add the correct total Inventory entry below.
            new_ledger_entries = []
            for le in ledger_entries:
                if le['ledger_name'] != 'Inventory' and le['ledger_name'] != 'Cost of Goods Sold':
                    new_ledger_entries.append(le)
            ledger_entries = new_ledger_entries
            
            if total_item_amount > 0:
                if voucher_type == 'Purchase':
                    ledger_entries.append({'ledger_name': 'Inventory', 'amount': total_item_amount, 'type': 'Debit'})
                else: 
                    ledger_entries.append({'ledger_name': 'Inventory', 'amount': total_item_amount, 'type': 'Credit'})

            # Handle VAT for Purchase/Purchase Return
            total_vat = sum(float(e.get('vat_amount') or 0) for e in item_entries)
            if total_vat > 0:
                vat_ledger_name = "Input VAT 5%"
                # Check if already present to avoid double counting if frontend sends it
                if not any(le['ledger_name'] == vat_ledger_name for le in ledger_entries):
                    if voucher_type == 'Purchase':
                        ledger_entries.append({'ledger_name': vat_ledger_name, 'amount': total_vat, 'type': 'Debit'})
                    else: # Purchase Return
                        ledger_entries.append({'ledger_name': vat_ledger_name, 'amount': total_vat, 'type': 'Credit'})

        if voucher_type in ('Sales','Sales Return'):
            _ensure_core_ledgers()
            cogs_total = 0.0
            adjusted_items = []
            
            sales_ledger_map = {}
            item_ledgers = set()

            for entry in item_entries:
                e = dict(entry)
                e['_location_override'] = e.get('_location_override', location_name)
                
                qty = abs(float(e.get('quantity') or 0))
                price = float(e.get('unit_price') or 0)
                line_amt = round(qty * price, 2)
                
                l_name = e.get('ledger_name')
                if not l_name:
                     l_name = 'Sales' if voucher_type == 'Sales' else 'Sales Return'
                
                sales_ledger_map[l_name] = sales_ledger_map.get(l_name, 0.0) + line_amt
                item_ledgers.add(l_name)

                c_rate, c_amt = _compute_cogs_for_entry(voucher_type, date, e, cursor, company_id=company_id)
                e['cogs_rate'] = c_rate
                e['cogs_amount'] = c_amt
                cogs_total += float(c_amt or 0)
                adjusted_items.append(e)
            item_entries = adjusted_items
            
            new_ledger_entries = []
            for le in ledger_entries:
                if le['ledger_name'] not in item_ledgers:
                    new_ledger_entries.append(le)
            ledger_entries = new_ledger_entries

            for l_name, amt in sales_ledger_map.items():
                if amt > 0:
                    if voucher_type == 'Sales':
                        ledger_entries.append({'ledger_name': l_name, 'amount': amt, 'type': 'Credit'})
                    else: 
                        ledger_entries.append({'ledger_name': l_name, 'amount': amt, 'type': 'Debit'})

            if cogs_total > 0:
                if voucher_type == 'Sales':
                    ledger_entries.append({'ledger_name': 'Cost of Goods Sold', 'amount': cogs_total, 'type': 'Debit'})
                    ledger_entries.append({'ledger_name': 'Inventory', 'amount': cogs_total, 'type': 'Credit'})
                else:
                    ledger_entries.append({'ledger_name': 'Cost of Goods Sold', 'amount': cogs_total, 'type': 'Credit'})

            # Handle VAT for Sales/Sales Return
            total_vat = sum(float(e.get('vat_amount') or 0) for e in item_entries)
            if total_vat > 0:
                vat_ledger_name = "Output VAT 5%"
                if not any(le['ledger_name'] == vat_ledger_name for le in ledger_entries):
                    if voucher_type == 'Sales':
                        ledger_entries.append({'ledger_name': vat_ledger_name, 'amount': total_vat, 'type': 'Credit'})
                    else: # Sales Return
                        ledger_entries.append({'ledger_name': vat_ledger_name, 'amount': total_vat, 'type': 'Debit'})

        if voucher_type == 'Purchase Return' and linked_voucher_number:
            try:
                cursor.execute("SELECT item_name, unit_price, landed_cost_per_unit FROM item_entries WHERE company_id = %s AND voucher_number = %s", (company_id, linked_voucher_number))
                orig_rows = cursor.fetchall()
                orig_map = {r[0]: {'unit_price': r[1], 'landed_cost': r[2] if r[2] is not None else r[1]} for r in orig_rows}
                
                total_unrecovered = 0.0
                total_original_debit_adjustment = 0.0
                
                inventory_ledgers = set(entry['ledger_name'] for entry in item_entries)
                
                for entry in item_entries:
                    item_name = entry['item_name']
                    if item_name in orig_map:
                        orig = orig_map[item_name]
                        landed = orig['landed_cost']
                        orig_price = orig['unit_price']
                        
                        qty = entry['quantity']
                        current_form_amount = entry['amount']
                        target_landed_amount = landed * qty
                        target_original_amount = orig_price * qty
                        
                        diff_per_unit = landed - orig_price
                        unrecovered = diff_per_unit * qty
                        
                        supplier_adjustment = current_form_amount - target_original_amount
                        total_original_debit_adjustment += supplier_adjustment
                        
                        total_unrecovered += unrecovered
                            
                        entry['unit_price'] = landed
                        entry['amount'] = target_landed_amount
                        entry['landed_cost_per_unit'] = landed 
                
                total_inventory_credit_needed = sum(e['amount'] for e in item_entries)
                
                inv_entry_found = False
                for le in ledger_entries:
                    if le['ledger_name'] == 'Inventory' and le['type'] == 'Credit':
                        le['amount'] = total_inventory_credit_needed
                        inv_entry_found = True
                        break
                
                if not inv_entry_found and total_inventory_credit_needed > 0:
                     ledger_entries.append({'ledger_name': 'Inventory', 'amount': total_inventory_credit_needed, 'type': 'Credit'})

                party_entry_found = False
                for le in ledger_entries:
                    if le['type'] == 'Debit' and le['ledger_name'] not in ('Inventory', 'Loss on Purchase Return – Charges'):
                        le['amount'] -= total_original_debit_adjustment
                        party_entry_found = True
                        break
                
                if total_unrecovered > 0:
                    ledger_entries.append({'ledger_name': 'Loss on Purchase Return – Charges', 'amount': total_unrecovered, 'type': 'Debit'})
                    
            except Exception as e:
                print(f"Error processing Purchase Return Landed Cost: {e}")

        total_debit = sum(entry['amount'] for entry in ledger_entries if entry['type'] == 'Debit')
        total_credit = sum(entry['amount'] for entry in ledger_entries if entry['type'] == 'Credit')
        # Note: for Opening vouchers the item totals are already reflected in the
        # auto-added Inventory ledger debit, so item_entries must NOT be added again
        # (doing so doubled the voucher amount and triggered false imbalance warnings).

        if voucher_type != 'Physical Stock' and abs(total_debit - total_credit) > 0.05:
            try:
                print('BalanceCheck', voucher_type, 'DEBIT=', total_debit, 'CREDIT=', total_credit, 'LEDGERS=', ledger_entries)
            except Exception:
                pass
        
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
        elif voucher_type == "Reversal":
            prefix = "REV"
        elif voucher_type == "Opening":
            prefix = "OPEN"
        else:
            prefix = voucher_type.upper()[:3]
        
        if credit_days and not due_date:
            try:
                from datetime import timedelta
                date_obj = datetime.strptime(date, '%Y-%m-%d')
                due_date_obj = date_obj + timedelta(days=int(credit_days))
                due_date = due_date_obj.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                pass

        cursor.execute("SELECT voucher_number FROM vouchers WHERE company_id = %s AND voucher_number LIKE %s ORDER BY length(voucher_number) DESC, voucher_number DESC LIMIT 1", (company_id, f"{prefix}-%"))
        row = cursor.fetchone()
        if row:
            last_no = row['voucher_number'] if isinstance(row, dict) or hasattr(row, 'keys') else row[0]
            try:
                parts = last_no.split('-')
                last_seq = int(parts[-1])
                new_seq = last_seq + 1
            except (ValueError, IndexError):
                new_seq = 1
        else:
            new_seq = 1

        voucher_number = f"{prefix}-{str(new_seq).zfill(5)}"
        cursor.execute("SELECT COALESCE(MAX(voucher_id),0)+1 as next_id FROM vouchers WHERE company_id = %s", (company_id,))
        row_vid = cursor.fetchone()
        if isinstance(row_vid, dict) or hasattr(row_vid, 'keys'):
             next_vid = row_vid['next_id'] or 1
        else:
             next_vid = row_vid[0] or 1
        total_amount = total_debit
        
        cursor.execute("""
            INSERT INTO vouchers (company_id, voucher_number, voucher_type, date, posting_date, amount, cost_center_code, narration, location_name, entry_date, voucher_id, credit_days, due_date, original_invoice_date, original_invoice_ref, linked_voucher_number)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (company_id, voucher_number, voucher_type, date, posting_date, total_amount, cost_center_code, narration, location_name, entry_date, next_vid, credit_days, due_date, original_invoice_date, original_invoice_ref, linked_voucher_number))
        
        if voucher_type != 'Inventory Transfer':
            for entry in ledger_entries:
                cursor.execute(
                    "INSERT INTO ledger_entries (company_id, voucher_number, ledger_name, amount, type, cost_center_code) VALUES (%s, %s, %s, %s, %s, %s)",
                    (company_id, voucher_number, entry['ledger_name'], entry['amount'], entry['type'], entry.get('cost_center_code'))
                )
                if entry['type'] == 'Debit':
                    cursor.execute(
                        "UPDATE ledgers SET closing_balance = closing_balance + %s WHERE company_id = %s AND ledger_name = %s",
                        (entry['amount'], company_id, entry['ledger_name'])
                    )
                else:
                    cursor.execute(
                        "UPDATE ledgers SET closing_balance = closing_balance - %s WHERE company_id = %s AND ledger_name = %s",
                        (entry['amount'], company_id, entry['ledger_name'])
                    )
        
        for entry in item_entries:
            cogs_rate = entry.get('cogs_rate')
            cogs_amount = entry.get('cogs_amount')
            
            r_qty = entry.get('running_qty')
            r_val = entry.get('running_value')
            r_wap = entry.get('running_wap')
            
            weight_kg = entry.get('weight_kg')
            landed_cost = entry.get('landed_cost_per_unit')
            if voucher_type == 'Purchase' and landed_cost is None:
                landed_cost = entry.get('unit_price')
            elif voucher_type == 'Purchase Return' and landed_cost is None:
                ref_v = entry.get('_ref_voucher_number') or entry.get('ref_voucher_number')
                if ref_v:
                    cursor.execute("SELECT landed_cost_per_unit, unit_price FROM item_entries WHERE company_id = %s AND voucher_number = %s AND item_name = %s", (company_id, ref_v, entry['item_name']))
                    row = cursor.fetchone()
                    if row:
                        if isinstance(row, dict) or hasattr(row, 'keys'):
                             landed_cost = row['landed_cost_per_unit'] if row['landed_cost_per_unit'] is not None else row['unit_price']
                        else:
                             landed_cost = row[0] if row[0] is not None else row[1]
                
            total_add_charges = entry.get('total_additional_charges_allocated') or 0.0

            cursor.execute("""
                INSERT INTO item_entries
                (company_id, voucher_number, item_name, quantity, unit_price, amount, ledger_name, type, location_name, cost_center_code, cogs_rate, cogs_amount, ref_voucher_number, running_qty, running_value, running_wap, weight_kg, landed_cost_per_unit, total_additional_charges_allocated)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                company_id,
                voucher_number,
                entry['item_name'],
                entry['quantity'],
                entry['unit_price'],
                entry['amount'],
                entry['ledger_name'],
                entry['type'],
                entry.get('_location_override', location_name),
                entry.get('cost_center_code'),
                cogs_rate,
                cogs_amount,
                entry.get('_ref_voucher_number'),
                r_qty,
                r_val,
                r_wap,
                weight_kg,
                landed_cost,
                total_add_charges
            ))

            if voucher_type == 'Inventory Transfer':
                pass
            elif voucher_type == 'Stock Adjustment':
                qty_mag = abs(entry['quantity'])
                if entry['type'] == 'Debit':
                    cursor.execute(
                        "UPDATE inventory SET stock_quantity = stock_quantity + %s WHERE company_id = %s AND name = %s",
                        (qty_mag, company_id, entry['item_name'])
                    )
                else:
                    cursor.execute(
                        "UPDATE inventory SET stock_quantity = stock_quantity - %s WHERE company_id = %s AND name = %s",
                        (qty_mag, company_id, entry['item_name'])
                    )
            else:
                if entry['type'] == 'Debit':
                    cursor.execute(
                        "UPDATE inventory SET stock_quantity = stock_quantity + %s WHERE company_id = %s AND name = %s",
                        (entry['quantity'], company_id, entry['item_name'])
                    )
                else:
                    cursor.execute(
                        "UPDATE inventory SET stock_quantity = stock_quantity - %s WHERE company_id = %s AND name = %s",
                        (entry['quantity'], company_id, entry['item_name'])
                    )
        
        if should_close:
            conn.commit()
        print(f"add_voucher: {voucher_number}, company={company_id}")
        
        if not skip_recalc:
            try:
                unique_items = set(entry['item_name'] for entry in item_entries)
                for item in unique_items:
                    recalculate_running_balance_for_item(item, date, company_id=company_id)
            except Exception as e:
                print(f"Error triggering running balance calc: {e}")
        
        return voucher_number
    except Exception as e:
        if should_close:
            conn.rollback()
        raise Exception(f"Error adding voucher: {str(e)}")
    finally:
        if should_close:
            conn.close()
def revalue_from(start_date, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT v.voucher_number, v.voucher_type, v.date
            FROM vouchers v
            WHERE v.company_id = %s AND v.date >= %s AND v.voucher_type IN ('Sales','Sales Return')
            ORDER BY v.date ASC, v.voucher_id ASC
            """,
            (company_id, start_date)
        )
        vouchers = cursor.fetchall()
        for vno, vtype, vdate in vouchers:
            cursor.execute(
                """
                SELECT id, item_name, quantity, location_name
                FROM item_entries
                WHERE company_id = %s AND voucher_number = %s
                """,
                (company_id, vno)
            )
            item_rows = cursor.fetchall()
            cogs_total = 0.0
            for iid, iname, qty, loc in item_rows:
                e = {'item_name': iname, 'quantity': qty, '_location_override': loc}
                cursor.execute("SELECT voucher_id FROM vouchers WHERE company_id = %s AND voucher_number = %s", (company_id, vno))
                row_vid = cursor.fetchone()
                vid = (row_vid['voucher_id'] if isinstance(row_vid, dict) or hasattr(row_vid, 'keys') else row_vid[0]) if row_vid else 0
                c_rate, c_amt = _compute_cogs_for_entry(vtype, vdate, e, cursor, voucher_id_cutoff=vid, company_id=company_id)
                cursor.execute("UPDATE item_entries SET cogs_rate = %s, cogs_amount = %s WHERE id = %s", (c_rate, c_amt, iid))
                cogs_total += float(c_amt or 0)
            cursor.execute("DELETE FROM ledger_entries WHERE company_id = %s AND voucher_number = %s AND ledger_name IN ('Cost of Goods Sold','Inventory')", (company_id, vno))
            if cogs_total > 0:
                if vtype == 'Sales':
                    cursor.execute("INSERT INTO ledger_entries (company_id, voucher_number, ledger_name, amount, type) VALUES (%s, %s, 'Cost of Goods Sold', %s, 'Debit')", (company_id, vno, cogs_total))
                    cursor.execute("INSERT INTO ledger_entries (company_id, voucher_number, ledger_name, amount, type) VALUES (%s, %s, 'Inventory', %s, 'Credit')", (company_id, vno, cogs_total))
                else:
                    cursor.execute("INSERT INTO ledger_entries (company_id, voucher_number, ledger_name, amount, type) VALUES (%s, %s, 'Inventory', %s, 'Debit')", (company_id, vno, cogs_total))
                    cursor.execute("INSERT INTO ledger_entries (company_id, voucher_number, ledger_name, amount, type) VALUES (%s, %s, 'Cost of Goods Sold', %s, 'Credit')", (company_id, vno, cogs_total))
        conn.commit()
        recompute_ledger_closing_balances(company_id=company_id)
    except Exception as e:
        conn.rollback()
        raise Exception(str(e))
    finally:
        conn.close()

def recompute_ledger_closing_balances(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT ledger_name, opening_balance, opening_balance_type FROM ledgers WHERE company_id = %s", (company_id,))
        ledgers = cursor.fetchall()
        for row in ledgers:
            if isinstance(row, dict) or hasattr(row, 'keys'):
                lname = row['ledger_name']
                opbal = row['opening_balance']
                opbt = row['opening_balance_type']
            else:
                lname, opbal, opbt = row

            params = [company_id, lname]
            ql = "SELECT SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE 0 END) as dr_sum, SUM(CASE WHEN le.type='Credit' THEN le.amount ELSE 0 END) as cr_sum FROM ledger_entries le WHERE le.company_id=%s AND le.ledger_name=%s"
            cursor.execute(ql, params)
            res = cursor.fetchone()
            if isinstance(res, dict) or hasattr(res, 'keys'):
                 ld = res['dr_sum']
                 lc = res['cr_sum']
            else:
                 ld = res[0]
                 lc = res[1]

            total_d = (opbal if (opbt or '')=='Debit' else 0) + (ld or 0)
            total_c = (opbal if (opbt or '')=='Credit' else 0) + (lc or 0)
            closing = total_d - total_c
            cursor.execute("UPDATE ledgers SET closing_balance = %s WHERE company_id = %s AND ledger_name = %s", (closing, company_id, lname))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()

def update_ledger_closing_balance(ledger_names, company_id=None, db_connection=None):
    """
    Update closing_balance for specific ledgers.
    More efficient than recomputing all ledgers.
    
    Args:
        ledger_names: List of ledger names or a single ledger name string
        company_id: Company ID (optional, will use current if not provided)
        db_connection: Database connection (optional, will create new if not provided)
    """
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return
    
    # Convert single string to list
    if isinstance(ledger_names, str):
        ledger_names = [ledger_names]
    
    if not ledger_names:
        return
    
    if db_connection:
        conn = db_connection
        should_close = False
    else:
        conn = get_connection()
        should_close = True
    
    cursor = conn.cursor()
    try:
        for lname in ledger_names:
            # Get opening balance
            cursor.execute("SELECT opening_balance, opening_balance_type FROM ledgers WHERE company_id = %s AND ledger_name = %s", 
                         (company_id, lname))
            row = cursor.fetchone()
            if not row:
                continue
            
            opbal, opbt = row
            
            # Calculate total debits and credits from ledger_entries
            cursor.execute("""
                SELECT SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE 0 END), 
                       SUM(CASE WHEN le.type='Credit' THEN le.amount ELSE 0 END) 
                FROM ledger_entries le 
                WHERE le.company_id=%s AND le.ledger_name=%s
            """, (company_id, lname))
            ld, lc = cursor.fetchone()
            
            # Calculate closing balance
            total_d = (opbal if (opbt or '')=='Debit' else 0) + (ld or 0)
            total_c = (opbal if (opbt or '')=='Credit' else 0) + (lc or 0)
            closing = total_d - total_c
            
            # Update closing_balance
            cursor.execute("UPDATE ledgers SET closing_balance = %s WHERE company_id = %s AND ledger_name = %s", 
                         (closing, company_id, lname))
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Error in update_ledger_closing_balance: {e}")
    finally:
        if should_close:
            conn.close()

def recalculate_running_balance_for_item(item_name, start_date=None, company_id=None, db_connection=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return

    if db_connection:
        conn = db_connection
        should_close = False
    else:
        conn = get_connection()
        should_close = True
        
    cursor = conn.cursor()
    try:
        running_qty = 0.0
        running_value = 0.0
        
        # Track affected ledgers for closing_balance update
        affected_ledgers = set()
        
        query_filter = ""
        params = [company_id, item_name]
        
        if start_date:
            cursor.execute("""
                SELECT running_qty, running_value 
                FROM item_entries ie
                JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
                WHERE ie.company_id = %s AND ie.item_name = %s AND v.date < %s
                ORDER BY v.date DESC, v.voucher_id DESC, ie.id DESC
                LIMIT 1
            """, (company_id, item_name, start_date))
            row = cursor.fetchone()
            if row:
                running_qty = row[0] or 0.0
                running_value = row[1] or 0.0
            
            query_filter = " AND v.date >= %s"
            params.append(start_date)
        
        sql = f"""
            SELECT ie.id, ie.quantity, ie.unit_price, ie.amount, ie.type, v.voucher_type, v.date, ie.voucher_number, ie.landed_cost_per_unit
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE ie.company_id = %s AND ie.item_name = %s {query_filter}
            ORDER BY v.date ASC, v.voucher_id ASC, ie.id ASC
        """
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        
        affected_vouchers = set()
        transfer_wap_map = {}

        # Batch update lists
        batch_updates_sales_return = []
        batch_updates_running = []
        batch_updates_sales_cogs = []
        batch_updates_amount = []
        batch_updates_price_amount = []

        for r in rows:
            ie_id, qty, unit_price, amount, ie_type, v_type, v_date, v_no, landed_cost = r
            qty = abs(qty or 0)
            unit_price = unit_price or 0
            effective_price = landed_cost if landed_cost is not None else unit_price
            
            current_wap = running_value / running_qty if running_qty != 0 else 0.0
            
            if v_type in ['Purchase', 'Opening', 'Sales Return']:
                if v_type == 'Sales Return':
                    price = current_wap if running_qty != 0 else unit_price
                elif v_type == 'Opening':
                    price = unit_price
                    if running_qty == 0:
                        running_value = 0
                else:
                    price = effective_price
                
                val_in = round(qty * price, 2)
                running_qty += qty
                running_value += val_in
                
                if v_type == 'Sales Return':
                    batch_updates_sales_return.append((price, val_in, running_qty, running_value, (running_value/running_qty if running_qty != 0 else 0), ie_id))
                    affected_vouchers.add((v_no, v_type))
                else:
                    batch_updates_running.append((running_qty, running_value, (running_value/running_qty if running_qty != 0 else 0), ie_id))
            
            elif v_type in ['Sales', 'Purchase Return']:
                cogs_rate = current_wap
                cogs_amount = round(qty * cogs_rate, 2)
                
                if v_type == 'Sales':
                    batch_updates_sales_cogs.append((cogs_rate, cogs_amount, ie_id))
                    affected_vouchers.add((v_no, v_type))
                
                    running_qty -= qty
                    running_value -= cogs_amount
                    
                    batch_updates_running.append((running_qty, running_value, current_wap, ie_id))

                elif v_type == 'Purchase Return':
                    # Purchase Return: Outward at FULL LANDED COST (Specific Identification), NOT WAP
                    # Falls back to unit_price if landed_cost is None
                    price_out = effective_price
                    val_out = round(qty * price_out, 2)
                    
                    running_qty -= qty
                    running_value -= val_out
                    
                    # Check if amount changed, if so, trigger GL update
                    if abs(amount - val_out) > 0.01:
                         batch_updates_amount.append((val_out, ie_id))
                         affected_vouchers.add((v_no, v_type))
                    
                    batch_updates_running.append((running_qty, running_value, (running_value/running_qty if running_qty != 0 else 0), ie_id))

            elif v_type == 'Stock Adjustment':
                if ie_type == 'Debit':
                    price = current_wap if running_qty != 0 else unit_price
                    val_in = round(qty * price, 2)
                    
                    batch_updates_price_amount.append((price, val_in, ie_id))
                    
                    running_qty += qty
                    running_value += val_in
                else:
                    cogs_rate = current_wap
                    val_out = round(qty * cogs_rate, 2)
                    
                    batch_updates_price_amount.append((cogs_rate, val_out, ie_id))
                    
                    running_qty -= qty
                    running_value -= val_out
                
                # Always add to affected vouchers to sync GL
                affected_vouchers.add((v_no, v_type))
                
                batch_updates_running.append((running_qty, running_value, (running_value/running_qty if running_qty != 0 else 0), ie_id))

            elif v_type == 'Inventory Transfer':
                if ie_type == 'Credit':
                    transfer_wap_map[v_no] = current_wap
                    cogs_rate = current_wap
                    val_out = round(qty * cogs_rate, 2)
                    
                    batch_updates_price_amount.append((cogs_rate, val_out, ie_id))
                    
                    running_qty -= qty
                    running_value -= val_out
                    
                    batch_updates_running.append((running_qty, running_value, current_wap, ie_id))
                else:
                    src_wap = transfer_wap_map.get(v_no, current_wap)
                    val_in = round(qty * src_wap, 2)
                    
                    batch_updates_price_amount.append((src_wap, val_in, ie_id))
                    
                    running_qty += qty
                    running_value += val_in
                    
                    batch_updates_running.append((running_qty, running_value, (running_value/running_qty if running_qty != 0 else 0), ie_id))

        # Execute batches
        if batch_updates_sales_return:
            cursor.executemany("UPDATE item_entries SET cogs_rate=%s, cogs_amount=%s, running_qty=%s, running_value=%s, running_wap=%s WHERE id=%s", batch_updates_sales_return)
        if batch_updates_running:
            cursor.executemany("UPDATE item_entries SET running_qty=%s, running_value=%s, running_wap=%s WHERE id=%s", batch_updates_running)
        if batch_updates_sales_cogs:
            cursor.executemany("UPDATE item_entries SET cogs_rate=%s, cogs_amount=%s WHERE id=%s", batch_updates_sales_cogs)
        if batch_updates_amount:
            cursor.executemany("UPDATE item_entries SET amount=%s WHERE id=%s", batch_updates_amount)
        if batch_updates_price_amount:
            cursor.executemany("UPDATE item_entries SET unit_price=%s, amount=%s WHERE id=%s", batch_updates_price_amount)

        # Update GL for affected vouchers
        for v_no, v_type in affected_vouchers:
            if v_type in ('Sales', 'Sales Return'):
                cursor.execute("SELECT SUM(cogs_amount) FROM item_entries WHERE company_id = %s AND voucher_number = %s", (company_id, v_no))
                new_cogs_total = cursor.fetchone()[0] or 0.0
                
                cursor.execute("DELETE FROM ledger_entries WHERE company_id = %s AND voucher_number = %s AND ledger_name IN ('Cost of Goods Sold','Inventory')", (company_id, v_no))
                
                # Track affected ledgers
                affected_ledgers.add('Inventory')
                affected_ledgers.add('Cost of Goods Sold')
                
                if new_cogs_total > 0:
                    if v_type == 'Sales':
                        cursor.execute("INSERT INTO ledger_entries (company_id, voucher_number, ledger_name, amount, type) VALUES (%s, %s, 'Cost of Goods Sold', %s, 'Debit')", (company_id, v_no, new_cogs_total))
                        cursor.execute("INSERT INTO ledger_entries (company_id, voucher_number, ledger_name, amount, type) VALUES (%s, %s, 'Inventory', %s, 'Credit')", (company_id, v_no, new_cogs_total))
                    elif v_type == 'Sales Return':
                        cursor.execute("INSERT INTO ledger_entries (company_id, voucher_number, ledger_name, amount, type) VALUES (%s, %s, 'Inventory', %s, 'Debit')", (company_id, v_no, new_cogs_total))
                        cursor.execute("INSERT INTO ledger_entries (company_id, voucher_number, ledger_name, amount, type) VALUES (%s, %s, 'Cost of Goods Sold', %s, 'Credit')", (company_id, v_no, new_cogs_total))

            elif v_type == 'Stock Adjustment':
                cursor.execute("SELECT SUM(amount) FROM item_entries WHERE company_id = %s AND voucher_number = %s AND type = 'Debit'", (company_id, v_no))
                debit_total = cursor.fetchone()[0] or 0.0
                cursor.execute("SELECT SUM(amount) FROM item_entries WHERE company_id = %s AND voucher_number = %s AND type = 'Credit'", (company_id, v_no))
                credit_total = cursor.fetchone()[0] or 0.0

                # Retrieve existing ledgers to know which is Expense/Income side (non-Inventory)
                cursor.execute("SELECT ledger_name, type, amount FROM ledger_entries WHERE company_id = %s AND voucher_number = %s", (company_id, v_no))
                existing_rows = cursor.fetchall()
                
                # Track all affected ledgers from this voucher
                for lname, _, _ in existing_rows:
                    affected_ledgers.add(lname)
                
                # Identify the "Counter" ledger(s) (i.e. Expense or Income ledger)
                # By definition, Stock Adjustment has Inventory + One or more Ledgers.
                # We need to preserve the ledger names but update their amounts.
                # Strategy: 
                # 1. Total Inventory Impact = Debit Total (Stock In) vs Credit Total (Stock Out).
                # 2. Net Inventory Change = Debit - Credit.
                #    If Net > 0 (Stock In): Inventory Dr, Income Cr.
                #    If Net < 0 (Stock Out): Expense Dr, Inventory Cr.
                # BUT, a voucher might have mixed lines.
                # Simplest robust approach:
                # Re-sum Inventory Leger entries based on Item Entries totals.
                # Scale Counter Ledger entries based on the change? Or just Replace if 1:1?
                # Risk: If multiple expense ledgers, how to distribute new amount?
                # Assumption: Stock Adjustment usually has 1 expense/income ledger per voucher or per line.
                # Let's assume proportional adjustment if multiple, or simple total if single.
                
                # BETTER SAFE APPROACH:
                # Just update the INVENTORY ledger entry. 
                # And the Balancing Entry? It MUST balance.
                # If we change Inventory Value, we MUST change Expense/Income Value.
                # Let's find the non-Inventory ledger entries and update them.
                
                non_inventory_entries = [r for r in existing_rows if r[0] != 'Inventory']
                
                if not non_inventory_entries:
                    continue # Should not happen if well-formed
                    
                # Calculate required totals
                # Inventory Debit needed = debit_total
                # Inventory Credit needed = credit_total
                
                # Delete all entries to rewrite cleanly
                cursor.execute("DELETE FROM ledger_entries WHERE company_id = %s AND voucher_number = %s", (company_id, v_no))
                
                # Insert Inventory Entries
                if debit_total > 0:
                     cursor.execute("INSERT INTO ledger_entries (company_id, voucher_number, ledger_name, amount, type) VALUES (%s, %s, 'Inventory', %s, 'Debit')", (company_id, v_no, debit_total))
                if credit_total > 0:
                     cursor.execute("INSERT INTO ledger_entries (company_id, voucher_number, ledger_name, amount, type) VALUES (%s, %s, 'Inventory', %s, 'Credit')", (company_id, v_no, credit_total))
                
                # Re-insert Non-Inventory Entries (Balancing)
                # We need to know which side they are on.
                # If Inventory Debit (Stock In) increased, then Income Credit must increase.
                # Total Debit must equal Total Credit.
                # Current Item Total Debit = debit_total. Item Total Credit = credit_total.
                # So we need Counter-Credit = debit_total. Counter-Debit = credit_total.
                
                # Distribute `debit_total` to Credit-side counter ledgers
                # Distribute `credit_total` to Debit-side counter ledgers
                
                chk_debit_counter = sum(r[2] for r in non_inventory_entries if r[1] == 'Debit')
                chk_credit_counter = sum(r[2] for r in non_inventory_entries if r[1] == 'Credit')
                
                # Avoid divide by zero
                # If multiple ledgers, we proportionally scale.
                
                for lname, ltype, lamt in non_inventory_entries:
                    new_amt = 0.0
                    if ltype == 'Debit': # Expense (balancing Inventory Credit)
                        if chk_debit_counter > 0:
                            ratio = lamt / chk_debit_counter
                            new_amt = round(credit_total * ratio, 2)
                        else:
                            new_amt = credit_total # Fallback if single
                    else: # Credit (Income balancing Inventory Debit)
                        if chk_credit_counter > 0:
                            ratio = lamt / chk_credit_counter
                            new_amt = round(debit_total * ratio, 2)
                        else:
                            new_amt = debit_total

                    if new_amt > 0:
                        cursor.execute("INSERT INTO ledger_entries (company_id, voucher_number, ledger_name, amount, type) VALUES (%s, %s, %s, %s, %s)", (company_id, v_no, lname, new_amt, ltype))

            elif v_type == 'Purchase Return':
                 # Sync Purchase Return GL if needed (Inventory Credit amount changed)
                 # Purchase Return: Party (Debit), Inventory (Credit) [at Cost]
                 # We need to update both.
                 
                 cursor.execute("SELECT SUM(amount) FROM item_entries WHERE company_id = %s AND voucher_number = %s", (company_id, v_no))
                 new_inv_credit = cursor.fetchone()[0] or 0.0

                 cursor.execute("SELECT amount FROM ledger_entries WHERE company_id = %s AND voucher_number = %s AND ledger_name = 'Inventory' AND type = 'Credit'", (company_id, v_no))
                 row = cursor.fetchone()
                 current_inv_credit = row[0] if row else 0.0
                 
                 if abs(current_inv_credit - new_inv_credit) > 0.01:
                      # Track Inventory as affected
                      affected_ledgers.add('Inventory')
                      
                      # 1. Update Inventory Credit
                      cursor.execute("UPDATE ledger_entries SET amount = %s WHERE company_id = %s AND voucher_number = %s AND ledger_name = 'Inventory' AND type = 'Credit'", (new_inv_credit, company_id, v_no))

                      # Track the party ledger as affected (need to query which one it is)
                      cursor.execute("SELECT ledger_name FROM ledger_entries WHERE company_id = %s AND voucher_number = %s AND type = 'Debit' AND ledger_name NOT IN ('Inventory', 'Cost of Goods Sold') AND ledger_name NOT LIKE 'Loss on Purchase Return%%'", (company_id, v_no))
                      party_rows = cursor.fetchall()
                      for (party_lname,) in party_rows:
                          affected_ledgers.add(party_lname)
                      
                      # 2. Update Party Debit
                      # We assume the Party Ledger is the one with Debit (and not additional charges etc if any).
                      # Actually, Purchase Return might have other debits/credits?
                      # Safest is to update the MAIN Party ledger.
                      # We can find it by Excluding 'Inventory' and 'Loss...' and looking for Debit.
                      
                      diff = new_inv_credit - current_inv_credit
                      # If diff +ve, we increase Party Debit.
                      
                      cursor.execute("UPDATE ledger_entries SET amount = amount + %s WHERE company_id = %s AND voucher_number = %s AND type = 'Debit' AND ledger_name NOT IN ('Inventory', 'Cost of Goods Sold') AND ledger_name NOT LIKE 'Loss on Purchase Return%%'", (diff, company_id, v_no))

        # Update Inventory Table with final Running Balance
        cursor.execute("UPDATE inventory SET stock_quantity = %s, stock_value = %s WHERE company_id = %s AND name = %s", 
                       (running_qty, running_value, company_id, item_name))

        # Update closing_balance for all affected ledgers
        if affected_ledgers:
            update_ledger_closing_balance(list(affected_ledgers), company_id=company_id, db_connection=conn)

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Error in recalculate_running_balance_for_item: {e}")
    finally:
        if should_close:
            conn.close()

def delete_voucher(voucher_number, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Check if exists
        cursor.execute("SELECT voucher_type, date FROM vouchers WHERE company_id = %s AND voucher_number = %s", (company_id, voucher_number))
        row = cursor.fetchone()
        if not row:
            raise Exception("Voucher not found")
        v_type, v_date = row
        
        # Check dependency: Linked Vouchers (e.g., Purchase Return linked to Purchase)
        cursor.execute("SELECT voucher_number FROM vouchers WHERE company_id = %s AND linked_voucher_number = %s", (company_id, voucher_number))
        linked = cursor.fetchall()
        if linked:
            linked_nos = ", ".join([r[0] for r in linked])
            raise Exception(f"Cannot delete voucher {voucher_number} because it is linked to: {linked_nos}")
            
        # 1. Reverse Inventory Impact (before deleting)
        cursor.execute("SELECT item_name, quantity, type FROM item_entries WHERE company_id = %s AND voucher_number = %s", (company_id, voucher_number))
        items = cursor.fetchall()
        for item_name, qty, type_ in items:
            qty = abs(qty)
            if v_type == 'Stock Adjustment':
                if type_ == 'Debit': # Was Inward -> Reduce Stock
                    cursor.execute("UPDATE inventory SET stock_quantity = stock_quantity - %s WHERE company_id = %s AND name = %s", (qty, company_id, item_name))
                else: # Was Outward -> Increase Stock
                    cursor.execute("UPDATE inventory SET stock_quantity = stock_quantity + %s WHERE company_id = %s AND name = %s", (qty, company_id, item_name))
            else:
                if type_ == 'Debit': # Was Inward -> Reduce Stock
                    cursor.execute("UPDATE inventory SET stock_quantity = stock_quantity - %s WHERE company_id = %s AND name = %s", (qty, company_id, item_name))
                else: # Was Outward -> Increase Stock
                    cursor.execute("UPDATE inventory SET stock_quantity = stock_quantity + %s WHERE company_id = %s AND name = %s", (qty, company_id, item_name))

        # 2. Reverse Ledger Impact
        cursor.execute("SELECT ledger_name, amount, type FROM ledger_entries WHERE company_id = %s AND voucher_number = %s", (company_id, voucher_number))
        ledgers_entries = cursor.fetchall()
        for lname, amt, ltype in ledgers_entries:
            if ltype == 'Debit':
                cursor.execute("UPDATE ledgers SET closing_balance = closing_balance - %s WHERE company_id = %s AND ledger_name = %s", (amt, company_id, lname))
            else:
                cursor.execute("UPDATE ledgers SET closing_balance = closing_balance + %s WHERE company_id = %s AND ledger_name = %s", (amt, company_id, lname))

        # 3. Delete Entries
        cursor.execute("DELETE FROM additional_charge_entries WHERE company_id = %s AND voucher_number = %s", (company_id, voucher_number))
        cursor.execute("DELETE FROM item_entries WHERE company_id = %s AND voucher_number = %s", (company_id, voucher_number))
        cursor.execute("DELETE FROM ledger_entries WHERE company_id = %s AND voucher_number = %s", (company_id, voucher_number))
        cursor.execute("DELETE FROM vouchers WHERE company_id = %s AND voucher_number = %s", (company_id, voucher_number))
        
        conn.commit()
        
        # Recalculate WAP for affected items
        unique_items = set(i[0] for i in items)
        for item in unique_items:
            recalculate_running_balance_for_item(item, v_date, company_id=company_id)
            
    except Exception as e:
        conn.rollback()
        raise Exception(f"Error deleting voucher: {str(e)}")
    finally:
        conn.close()

def get_voucher_details(voucher_number, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return None

    conn = get_connection()
    # conn.row_factory = sqlite3.Row # Removed for Postgres
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM vouchers WHERE company_id = %s AND voucher_number = %s", (company_id, voucher_number))
        voucher = cursor.fetchone()
        if not voucher:
            return None
            
        cursor.execute("SELECT * FROM ledger_entries WHERE company_id = %s AND voucher_number = %s", (company_id, voucher_number))
        ledgers = cursor.fetchall()
        
        cursor.execute("SELECT * FROM item_entries WHERE company_id = %s AND voucher_number = %s", (company_id, voucher_number))
        items = cursor.fetchall()
        
        cursor.execute("SELECT * FROM additional_charge_entries WHERE company_id = %s AND voucher_number = %s", (company_id, voucher_number))
        charges = cursor.fetchall()
        
        return {
            'voucher': dict(voucher) if voucher else None,
            'ledger_entries': [dict(l) for l in ledgers],
            'item_entries': [dict(i) for i in items],
            'additional_charges': [dict(c) for c in charges]
        }
    finally:
        conn.close()

def update_voucher_entries(voucher_number, item_entries, ledger_entries, narration, company_id=None):
    # This is a complex operation: usually better to delete and recreate, or diff.
    # For now, let's implement a "replace entries" logic, assuming the caller handles validation.
    # BUT, recreating implies changing voucher number or ID? No.
    # Safer to Reuse add_voucher logic? 
    # For now, we will just update narration and basic fields if needed, 
    # but full update requires full logic of add_voucher (re-stocking, re-ledgering).
    # "Edit Voucher" usually involves:
    # 1. Reverse old voucher effects (stock, ledgers)
    # 2. Update voucher data
    # 3. Apply new voucher effects
    
    # Since we have delete_voucher and add_voucher, we can reuse them if we can preserve the number.
    # But add_voucher generates a new number.
    # So we should modify add_voucher to accept specific voucher_number?
    # Or write a specific update function.
    
    # Given the complexity and potential for bugs, and that 'Edit' wasn't explicitly requested in this specific turn 
    # (though it's a general feature), I'll leave this placeholder or basic implementation.
    # However, to be safe for "Update all database query functions", I should at least support company_id here.
    
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE vouchers SET narration = %s WHERE company_id = %s AND voucher_number = %s", (narration, company_id, voucher_number))
        conn.commit()
    finally:
        conn.close()

def create_closing_entry(fy_id, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

    fy = get_fy_by_id(fy_id, company_id=company_id)
    if not fy:
        raise Exception(f"Financial Year {fy_id} not found")

    end_date = fy['end_date']
    
    # Get Trial Balance as of FY End Date
    trial_balance, _, _ = get_trial_balance_data(as_of_date=end_date, company_id=company_id)
    
    # Identify Income and Expense Groups
    income_groups = ('Sales', 'Direct Income', 'Indirect Income')
    expense_groups = ('Purchase', 'Direct Expenses', 'Indirect Expenses')
    
    ledger_entries = []
    total_income = 0.0
    total_expense = 0.0
    
    # Process Ledgers
    for item in trial_balance:
        ledger_name = item['ledger_name']
        group_name = item['group_name']
        debit = float(item['debit'] or 0)
        credit = float(item['credit'] or 0)
        
        # Skip if no balance
        if abs(debit - credit) < 0.001:
            continue
            
        if group_name in income_groups:
            # Income usually has Credit balance. To close, we Debit it.
            amount = credit - debit 
            if amount > 0:
                ledger_entries.append({
                    'ledger_name': ledger_name,
                    'amount': amount,
                    'type': 'Debit'
                })
                total_income += amount
            elif amount < 0:
                ledger_entries.append({
                    'ledger_name': ledger_name,
                    'amount': abs(amount),
                    'type': 'Credit'
                })
                total_income += amount
                
        elif group_name in expense_groups:
            # Expense usually has Debit balance. To close, we Credit it.
            amount = debit - credit 
            if amount > 0:
                ledger_entries.append({
                    'ledger_name': ledger_name,
                    'amount': amount,
                    'type': 'Credit'
                })
                total_expense += amount
            elif amount < 0:
                ledger_entries.append({
                    'ledger_name': ledger_name,
                    'amount': abs(amount),
                    'type': 'Debit'
                })
                total_expense += amount

    if not ledger_entries:
        print(f"No income/expense entries to close for FY {fy['fy_code']}.")
        return

    # Calculate Net Profit / Loss
    net_profit = total_income - total_expense
    
    # Transfer to Reserve & Surplus
    if abs(net_profit) > 0.001:
        if net_profit > 0:
            # Profit: Credit Reserve & Surplus
            ledger_entries.append({
                'ledger_name': 'Reserve & Surplus',
                'amount': net_profit,
                'type': 'Credit'
            })
        else:
            # Loss: Debit Reserve & Surplus
            ledger_entries.append({
                'ledger_name': 'Reserve & Surplus',
                'amount': abs(net_profit),
                'type': 'Debit'
            })
    
    # Create Voucher
    add_voucher(
        voucher_type='Closing',
        date=end_date,
        ledger_entries=ledger_entries,
        item_entries=[],
        narration=f"Closing Entry for FY {fy['fy_code']}",
        company_id=company_id,
        allow_locked_fy=True
    )
    print(f"Created Closing Entry for FY {fy['fy_code']}")

def delete_closing_entry(fy_id, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

    fy = get_fy_by_id(fy_id, company_id=company_id)
    if not fy:
        raise Exception(f"Financial Year {fy_id} not found")

    # Find the Closing Voucher for this FY
    # Logic: Look for 'Closing' type voucher on the FY end_date with specific narration
    # This is a bit loose, ideally we'd link it better, but for now this matches create logic.
    
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Get voucher_number of the closing entry
        # We search by Type='Closing', Date=End Date, and Narration pattern
        narration_pattern = f"Closing Entry for FY {fy['fy_code']}%"
        
        cursor.execute("""
            SELECT voucher_number FROM vouchers 
            WHERE company_id = %s AND voucher_type = 'Closing' AND date = %s AND narration LIKE %s
        """, (company_id, fy['end_date'], narration_pattern))
        
        rows = cursor.fetchall()
        
        if not rows:
            print(f"No Closing Entry found for FY {fy['fy_code']} to delete.")
            return

        for row in rows:
            voucher_number = row[0]
            # Delete the voucher
            # We can use delete_voucher function if available, or do it directly.
            # Ideally use delete_voucher to handle ledger updates etc.
            # But wait, delete_voucher might check for locked FY.
            # Since we are Re-Opening, we must allow deletion even if FY was locked (though we just unlocked it in the caller).
            
            # Let's call the internal delete logic or simply delete_voucher.
            # The caller (reopen_financial_year) unlocks the FY BEFORE calling this.
            # So delete_voucher should work fine.
            delete_voucher(voucher_number, company_id=company_id)
            print(f"Deleted Closing Entry: {voucher_number}")
            
    except Exception as e:
        raise Exception(f"Error deleting closing entry: {str(e)}")
    finally:
        conn.close()
