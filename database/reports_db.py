"""
Reports module: Trial Balance, P&L, Balance Sheet, Stock Reports
"""
# import sqlite3 # Removed
from .config import get_connection
from datetime import datetime, timedelta
from .company_db import get_current_company_id

def to_date_str(d):
    """Ensure date is string for SQL text comparison."""
    if hasattr(d, 'strftime'):
        return d.strftime('%Y-%m-%d')
    return d

def is_latest_date(cursor, as_of_date, company_id):
    """Check if the provided date is effectively the latest date (no future vouchers)."""
    if not as_of_date:
        return True
    
    as_of_date_str = to_date_str(as_of_date)
    cursor.execute("SELECT 1 FROM vouchers WHERE company_id = %s AND date > %s LIMIT 1", (company_id, as_of_date_str))
    return cursor.fetchone() is None

def get_trial_balance_data(as_of_date=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return [], 0, 0

    as_of_date_str = to_date_str(as_of_date)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Optimization: If querying for the latest date, use stored closing balances
        if is_latest_date(cursor, as_of_date_str, company_id):
            print(f"get_trial_balance_data({as_of_date_str}): Using optimized stored balances.")
            cursor.execute("""
                SELECT l.ledger_name, g.group_name, l.closing_balance
                FROM ledgers l
                JOIN groups g ON l.group_code = g.group_code AND l.company_id = g.company_id
                WHERE l.company_id = %s AND l.closing_balance != 0
            """, (company_id,))
            rows = cursor.fetchall()
            trial_balance = []
            total_debit = 0.0
            total_credit = 0.0
            
            for ledger_name, group_name, closing_balance in rows:
                dr = closing_balance if closing_balance > 0 else 0.0
                cr = -closing_balance if closing_balance < 0 else 0.0
                trial_balance.append({
                    'ledger_name': ledger_name,
                    'group_name': group_name,
                    'debit': round(dr, 2),
                    'credit': round(cr, 2),
                })
                total_debit += dr
                total_credit += cr
            
            return trial_balance, round(total_debit, 2), round(total_credit, 2)

        # Optimization: Reverse Calculation for Backdated Reports
        # This avoids summing from the beginning of time (O(History)) and instead subtracts future transactions (O(Future)).
        # For recent dates (most common), this is significantly faster.
        print(f"get_trial_balance_data({as_of_date_str}): Using Reverse Calculation.")
        
        # 1. Get Current Balances
        cursor.execute("SELECT ledger_name, group_code, closing_balance FROM ledgers WHERE company_id = %s", (company_id,))
        ledgers = cursor.fetchall()
        
        # 2. Get Future Movements (Net)
        # Note: Ledger Entry 'Debit' increases balance, 'Credit' decreases balance
        cursor.execute("""
            SELECT le.ledger_name, SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE -le.amount END)
            FROM ledger_entries le
            JOIN vouchers v ON le.voucher_number = v.voucher_number AND le.company_id = v.company_id
            WHERE v.company_id = %s AND v.date > %s
            GROUP BY le.ledger_name
        """, (company_id, as_of_date_str))
        # Handle correct unpacking for Postgres DictCursor which returns rows, not tuples directly adaptable to dict() if row has no keys?
        # But cursor.fetchall() returns list of rows. dict() on list of rows expects (key, value) pairs.
        # If row is [name, sum], it works.
        future_movements = dict([(r[0], r[1]) for r in cursor.fetchall()])
        
        # 3. Get Groups Map
        cursor.execute("SELECT group_code, group_name FROM groups WHERE company_id = %s", (company_id,))
        groups = dict([(r[0], r[1]) for r in cursor.fetchall()])
        
        trial_balance = []
        total_debit = 0.0
        total_credit = 0.0
        
        for ledger_name, group_code, current_bal in ledgers:
            future_change = future_movements.get(ledger_name, 0.0)
            # Historical = Current - Future
            hist_bal = (current_bal or 0.0) - future_change
            
            if abs(hist_bal) > 0.001:
                dr = hist_bal if hist_bal > 0 else 0.0
                cr = -hist_bal if hist_bal < 0 else 0.0
                trial_balance.append({
                    'ledger_name': ledger_name,
                    'group_name': groups.get(group_code, ''),
                    'debit': round(dr, 2),
                    'credit': round(cr, 2),
                })
                total_debit += dr
                total_credit += cr
                
        return trial_balance, round(total_debit, 2), round(total_credit, 2)
    except Exception as e:
        print(f"Error in get_trial_balance_data: {str(e)}")
        return [], 0, 0
    finally:
        conn.close()

def get_ledger_transactions(ledger_name, from_date=None, to_date=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return [], 0.0

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # 1. Get Opening Balance (before from_date)
        cursor.execute("SELECT opening_balance, opening_balance_type FROM ledgers WHERE company_id = %s AND ledger_name = %s", (company_id, ledger_name))
        row = cursor.fetchone()
        if not row:
            # Ledger might not exist or no opening balance set
            op_bal = 0.0
            op_type = 'Debit'
        else:
            op_bal = row[0] or 0.0
            op_type = row[1] or 'Debit'
        
        running_balance = op_bal if op_type == 'Debit' else -op_bal
        
        if from_date:
            cursor.execute("""
                    SELECT SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE -le.amount END)
                    FROM ledger_entries le
                    JOIN vouchers v ON le.voucher_number = v.voucher_number AND le.company_id = v.company_id
                WHERE v.company_id = %s AND le.ledger_name = %s AND v.date < %s
            """, (company_id, ledger_name, from_date))
            pre_period_movement = cursor.fetchone()[0] or 0.0
            running_balance += pre_period_movement
            
        # 2. Get Transactions in Period
        query = """
            SELECT v.date, v.voucher_number, v.voucher_type, v.narration, le.amount, le.type
            FROM ledger_entries le
            JOIN vouchers v ON le.voucher_number = v.voucher_number AND le.company_id = v.company_id
            WHERE v.company_id = %s AND le.ledger_name = %s
        """
        params = [company_id, ledger_name]
        
        if from_date:
            query += " AND v.date >= %s"
            params.append(from_date)
        if to_date:
            query += " AND v.date <= %s"
            params.append(to_date)
            
        query += " ORDER BY v.date, v.voucher_id"
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        transactions = []
        
        current_bal = running_balance
        
        for r in rows:
            date, vn, vtype, narr, amt, type_ = r[0], r[1], r[2], r[3], r[4], r[5]
            debit = amt if type_ == 'Debit' else 0
            credit = amt if type_ == 'Credit' else 0
            
            if type_ == 'Debit':
                current_bal += amt
            else:
                current_bal -= amt
                
            transactions.append({
                'date': date,
                'voucher_number': vn,
                'voucher_type': vtype,
                'narration': narr,
                'debit': debit,
                'credit': credit,
                'balance': current_bal
            })
            
        return transactions, current_bal
        
    except Exception as e:
        print(f"Error in get_ledger_transactions: {e}")
        return [], 0.0
    finally:
        conn.close()

def get_coa_balances(from_date=None, to_date=None, company_id=None):
    """Return per-ledger net change and balance-at-date for the Chart of Accounts."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return {}

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Base: stored closing_balance (sum of all closing_balances should be 0 for balanced books)
        cursor.execute("SELECT ledger_name, closing_balance FROM ledgers WHERE company_id = %s", (company_id,))
        result = {}
        for ledger_name, closing_bal in cursor.fetchall():
            result[ledger_name] = {
                'net_change': 0.0,
                'balance': round(closing_bal or 0.0, 2),
            }

        # Net change: movements within the requested period
        nc_q = """
            SELECT le.ledger_name,
                   SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE -le.amount END)
            FROM ledger_entries le
            JOIN vouchers v ON le.voucher_number = v.voucher_number AND le.company_id = v.company_id
            WHERE v.company_id = %s
        """
        nc_params = [company_id]
        if from_date:
            nc_q += " AND v.date >= %s"
            nc_params.append(from_date)
        if to_date:
            nc_q += " AND v.date <= %s"
            nc_params.append(to_date)
        nc_q += " GROUP BY le.ledger_name"
        cursor.execute(nc_q, nc_params)
        orphan_nc = 0.0
        for ledger_name, net in cursor.fetchall():
            net = round(net or 0.0, 2)
            if ledger_name in result:
                result[ledger_name]['net_change'] = net
            else:
                # Entry references a ledger not in the ledgers table — accumulate so the total stays zero
                orphan_nc += net
        if abs(orphan_nc) > 0.001:
            result['__diff__'] = {'net_change': round(orphan_nc, 2), 'balance': 0.0}

        # Balance at date: reverse approach — subtract movements AFTER to_date from closing_balance.
        # This preserves the double-entry balance property: sum(closing_balance) - sum(post-period) = 0 - 0 = 0.
        if to_date:
            cursor.execute("""
                SELECT le.ledger_name,
                       SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE -le.amount END)
                FROM ledger_entries le
                JOIN vouchers v ON le.voucher_number = v.voucher_number AND le.company_id = v.company_id
                WHERE v.company_id = %s AND v.date > %s
                GROUP BY le.ledger_name
            """, (company_id, to_date))
            for ledger_name, post_mvmt in cursor.fetchall():
                if ledger_name in result:
                    result[ledger_name]['balance'] = round(
                        result[ledger_name]['balance'] - (post_mvmt or 0.0), 2)

        return result
    except Exception as e:
        print(f"Error in get_coa_balances: {e}")
        return {}
    finally:
        conn.close()


def get_negative_stock_items(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT item_code, name, stock_quantity FROM inventory WHERE company_id = %s AND stock_quantity < 0", (company_id,))
        return cursor.fetchall()
    except Exception as e:
        print(f"Error in get_negative_stock_items: {e}")
        return []
    finally:
        conn.close()

def get_voucher_register_data(voucher_type, from_date=None, to_date=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # 1. Fetch Vouchers
        params = [company_id]
        base_where = "WHERE v.company_id = %s"
        
        if voucher_type and voucher_type != 'All':
            base_where += " AND v.voucher_type = %s"
            params.append(voucher_type)
            
        if from_date:
            base_where += " AND v.date >= %s"
            params.append(from_date)
            
        if to_date:
            base_where += " AND v.date <= %s"
            params.append(to_date)
            
        query_vouchers = f"""
            SELECT v.voucher_number, v.date, v.amount, v.narration, v.voucher_type
            FROM vouchers v
            {base_where}
            ORDER BY v.date DESC, v.voucher_id DESC
        """
        
        cursor.execute(query_vouchers, params)
        vouchers_raw = cursor.fetchall()
        
        if not vouchers_raw:
             return []

        # 2. Fetch All Related Ledger Entries in one go
        # We assume the number of vouchers isn't exceeding memory limits for Python dicts.
        # Re-using the same WHERE clause on joined vouchers table is safer than IN clause for large sets.
        query_ledgers = f"""
            SELECT le.voucher_number, le.ledger_name, le.amount, le.type 
            FROM ledger_entries le
            JOIN vouchers v ON le.voucher_number = v.voucher_number AND le.company_id = v.company_id
            {base_where}
        """
        cursor.execute(query_ledgers, params) # Reuse params
        all_ledgers = cursor.fetchall()
        
        # Group Ledgers by Voucher Number
        ledgers_map = {}
        for vn, ln, la, lt in all_ledgers:
            if vn not in ledgers_map: ledgers_map[vn] = []
            ledgers_map[vn].append((ln, la, lt))

        # 3. Fetch All Related Item Entries in one go
        query_items = f"""
            SELECT ie.voucher_number, ie.item_name, ie.quantity, ie.unit_price, ie.amount 
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            {base_where}
        """
        cursor.execute(query_items, params) # Reuse params
        all_items = cursor.fetchall()
        
        # Group Items by Voucher Number
        items_map = {}
        for vn, iname, qty, rate, amt in all_items:
            if vn not in items_map: items_map[vn] = []
            items_map[vn].append({'name': iname, 'qty': qty, 'rate': rate, 'amount': amt})

        # 4. Construct Result
        vouchers = []
        for vn, date, amt, narr, vtype in vouchers_raw:
            ledgers = ledgers_map.get(vn, [])
            
            party_name = "Multiple"
            relevant_ledgers = []
            vat_amount = 0
            for ln, la, lt in ledgers:
                 if 'VAT' in ln.upper():
                     vat_amount += abs(la or 0)
                 if ln not in ('Sales', 'Purchase', 'Input VAT 5%', 'Output VAT 5%', 'Cost of Goods Sold', 'Inventory', 'Discount Allowed', 'Discount Received'):
                     relevant_ledgers.append(ln)
            
            if len(relevant_ledgers) == 1:
                party_name = relevant_ledgers[0]
            elif len(relevant_ledgers) > 1:
                party_name = ", ".join(relevant_ledgers[:2]) + ("..." if len(relevant_ledgers) > 2 else "")
            elif not relevant_ledgers and ledgers:
                 party_name = ledgers[0][0]

            items = items_map.get(vn, [])
            
            vouchers.append({
                'voucher_number': vn,
                'date': date,
                'amount': amt,
                'narration': narr,
                'party_name': party_name,
                'items': items,
                'voucher_type': vtype,
                'vat_amount': vat_amount
            })
            
        return vouchers
    except Exception as e:
        print(f"Error in get_voucher_register_data: {str(e)}")
        return []
    finally:
        conn.close()

def get_sales_summary_data(from_date=None, to_date=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    try:
        query = """
            SELECT ie.item_name, SUM(ABS(ie.quantity)), SUM(ABS(ie.amount))
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE v.company_id = %s AND v.voucher_type = 'Sales'
        """
        params = [company_id]
        if from_date:
            query += " AND v.date >= %s"
            params.append(from_date)
        if to_date:
            query += " AND v.date <= %s"
            params.append(to_date)
            
        query += " GROUP BY ie.item_name ORDER BY SUM(ABS(ie.amount)) DESC"
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [{'item_name': r[0], 'quantity': r[1], 'amount': r[2]} for r in rows]
    except Exception as e:
        print(f"Error in get_sales_summary_data: {e}")
        return []
    finally:
        conn.close()

def get_purchase_summary_data(from_date=None, to_date=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    try:
        query = """
            SELECT ie.item_name, SUM(ABS(ie.quantity)), SUM(ABS(ie.amount))
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE v.company_id = %s AND v.voucher_type = 'Purchase'
        """
        params = [company_id]
        if from_date:
            query += " AND v.date >= %s"
            params.append(from_date)
        if to_date:
            query += " AND v.date <= %s"
            params.append(to_date)
            
        query += " GROUP BY ie.item_name ORDER BY SUM(ABS(ie.amount)) DESC"
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [{'item_name': r[0], 'quantity': r[1], 'amount': r[2]} for r in rows]
    except Exception as e:
        print(f"Error in get_purchase_summary_data: {e}")
        return []
    finally:
        conn.close()

def get_vat_summary_data(from_date=None, to_date=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return {'output_vat': 0, 'input_vat': 0, 'net_vat': 0}

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Get all ledgers in Duties & Taxes (G011)
        # We classify Debit balance ledgers as Input VAT and Credit balance ledgers as Output VAT
        # But wait, a single ledger can have debit and credit entries.
        # Usually, Input VAT ledger has Debit entries (Purchase)
        # Output VAT ledger has Credit entries (Sales)
        # We will sum up all Debit entries in G011 as Input, and all Credit entries as Output.
        
        # However, 'Duties & Taxes' ledgers are Liabilities.
        # Credit increases Liability (Output VAT).
        # Debit decreases Liability (Input VAT / Payment).
        
        # So:
        # Total Credit in G011 = Output VAT
        # Total Debit in G011 = Input VAT
        
        query = """
            SELECT 
                SUM(CASE WHEN le.type='Credit' THEN le.amount ELSE 0 END) as total_credit,
                SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE 0 END) as total_debit
            FROM ledger_entries le
            JOIN vouchers v ON le.voucher_number = v.voucher_number AND le.company_id = v.company_id
            JOIN ledgers l ON le.ledger_name = l.ledger_name AND le.company_id = l.company_id AND l.company_id = v.company_id
            WHERE v.company_id = %s AND l.group_code = 'G011'
        """
        params = [company_id]
        if from_date:
            query += " AND v.date >= %s"
            params.append(from_date)
        if to_date:
            query += " AND v.date <= %s"
            params.append(to_date)
            
        cursor.execute(query, params)
        row = cursor.fetchone()
        
        # Output VAT is primarily Credit entries in Duties & Taxes
        output_vat = row[0] if row and row[0] else 0.0
        
        # Input VAT is primarily Debit entries in Duties & Taxes
        input_vat = row[1] if row and row[1] else 0.0
        
        # Net VAT Payable = Output - Input
        net_vat = output_vat - input_vat
        
        return {'output_vat': output_vat, 'input_vat': input_vat, 'net_vat': net_vat}
    except Exception as e:
        print(f"Error in get_vat_summary_data: {e}")
        return {'output_vat': 0, 'input_vat': 0, 'net_vat': 0}
    finally:
        conn.close()

def get_slow_moving_items(days_threshold=90, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cutoff_date = (datetime.now() - timedelta(days=int(days_threshold))).strftime('%Y-%m-%d')
        
        cursor.execute("SELECT name, stock_quantity, stock_value FROM inventory WHERE company_id = %s AND stock_quantity > 0", (company_id,))
        stock_items = cursor.fetchall()
        
        slow_moving = []
        for name, qty, val in stock_items:
            cursor.execute("""
                SELECT MAX(v.date)
                FROM item_entries ie
                JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
                WHERE v.company_id = %s AND ie.item_name = %s AND v.voucher_type = 'Sales'
            """, (company_id, name))
            last_sale = cursor.fetchone()[0]
            
            if not last_sale or last_sale < cutoff_date:
                slow_moving.append({
                    'item_name': name,
                    'stock_quantity': qty,
                    'stock_value': val,
                    'last_sale_date': last_sale or 'Never'
                })
        return slow_moving
    except Exception as e:
        print(f"Error in get_slow_moving_items: {e}")
        return []
    finally:
        conn.close()


def get_ageing_report_data(group_code, as_of_date=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    try:
        if not as_of_date:
            as_of_date = datetime.today().strftime('%Y-%m-%d')
            
        # 1. Get Ledgers in Group
        cursor.execute("SELECT ledger_name, closing_balance FROM ledgers WHERE company_id = %s AND group_code = %s", (company_id, group_code))
        ledgers = cursor.fetchall()
        
        report_data = []
        
        for ledger_name, current_bal in ledgers:
            # Calculate Balance as of as_of_date
            # Bal = Current - Future Movements
            cursor.execute("""
                SELECT SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE -le.amount END)
                FROM ledger_entries le
                JOIN vouchers v ON le.voucher_number = v.voucher_number AND le.company_id = v.company_id
                WHERE v.company_id = %s AND le.ledger_name = %s AND v.date > %s
            """, (company_id, ledger_name, as_of_date))
            row = cursor.fetchone()
            future_change = row[0] if row and row[0] is not None else 0.0
            balance = current_bal - future_change
            
            if abs(balance) < 0.01:
                continue
                
            # Determine direction
            is_debit_balance = balance > 0
            target_type = 'Debit' if is_debit_balance else 'Credit'
            
            # Fetch all entries of target_type, sorted DESC by Date (Newest first)
            cursor.execute("""
                SELECT v.date, v.due_date, le.amount, v.voucher_number
                FROM ledger_entries le
                JOIN vouchers v ON le.voucher_number = v.voucher_number AND le.company_id = v.company_id
                WHERE v.company_id = %s AND le.ledger_name = %s AND le.type = %s AND v.date <= %s
                ORDER BY v.date DESC, v.voucher_id DESC
            """, (company_id, ledger_name, target_type, as_of_date))
            
            entries = cursor.fetchall()
            
            outstanding_amount = abs(balance)
            breakdown = {
                'not_due': 0.0,
                '0_30': 0.0,
                '31_60': 0.0,
                '61_90': 0.0,
                '90_plus': 0.0,
                'total': outstanding_amount
            }
            
            try:
                as_of_date_obj = datetime.strptime(as_of_date, '%Y-%m-%d')
            except ValueError:
                as_of_date_obj = datetime.today()
            
            for dt, due_dt, amt, vn in entries:
                if outstanding_amount <= 0.001:
                    break
                    
                alloc_amount = min(outstanding_amount, amt)
                outstanding_amount -= alloc_amount
                
                # Calculate Age
                ref_date_str = due_dt if due_dt else dt
                try:
                    ref_date = datetime.strptime(ref_date_str, '%Y-%m-%d')
                except (ValueError, TypeError):
                    try:
                        ref_date = datetime.strptime(dt, '%Y-%m-%d')
                    except:
                        ref_date = as_of_date_obj
                    
                days = (as_of_date_obj - ref_date).days
                
                if days < 0:
                    breakdown['not_due'] += alloc_amount
                elif days <= 30:
                    breakdown['0_30'] += alloc_amount
                elif days <= 60:
                    breakdown['31_60'] += alloc_amount
                elif days <= 90:
                    breakdown['61_90'] += alloc_amount
                else:
                    breakdown['90_plus'] += alloc_amount
            
            if outstanding_amount > 0.001:
                breakdown['90_plus'] += outstanding_amount

            for k in breakdown:
                breakdown[k] = round(breakdown[k], 2)
                
            report_data.append({
                'ledger_name': ledger_name,
                'balance': round(balance, 2),
                'buckets': breakdown
            })
            
        return report_data
    except Exception as e:
        print(f"Error in get_ageing_report_data: {e}")
        return []
    finally:
        conn.close()

def get_cash_flow_data(from_date, to_date, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return {}

    conn = get_connection()
    cursor = conn.cursor()
    try:
        def get_net_movement_by_mg(mg_codes, exclude_group_codes=None, ledger_name_like=None):
            placeholders = ','.join(['%s'] * len(mg_codes))
            params = [company_id] + list(mg_codes) + [from_date, to_date]
            
            query = f"""
                SELECT SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE -le.amount END)
                FROM ledger_entries le
                JOIN vouchers v ON le.voucher_number = v.voucher_number AND le.company_id = v.company_id
                JOIN ledgers l ON le.ledger_name = l.ledger_name AND le.company_id = l.company_id AND l.company_id = v.company_id
                JOIN groups g ON l.group_code = g.group_code AND g.company_id = v.company_id
                WHERE v.company_id = %s AND g.master_group_code IN ({placeholders})
                AND v.date >= %s AND v.date <= %s
                AND v.voucher_type NOT IN ('Closing', 'Opening')
            """
            
            if exclude_group_codes:
                ex_placeholders = ','.join(['%s'] * len(exclude_group_codes))
                query += f" AND l.group_code NOT IN ({ex_placeholders})"
                params.extend(exclude_group_codes)
            
            if ledger_name_like:
                query += " AND l.ledger_name LIKE %s"
                params.append(ledger_name_like)
                
            cursor.execute(query, params)
            result = cursor.fetchone()[0]
            return result or 0.0

        def get_net_movement_by_group(group_codes):
            placeholders = ','.join(['%s'] * len(group_codes))
            params = [company_id] + list(group_codes) + [from_date, to_date]
            query = f"""
                SELECT SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE -le.amount END)
                FROM ledger_entries le
                JOIN vouchers v ON le.voucher_number = v.voucher_number AND le.company_id = v.company_id
                JOIN ledgers l ON le.ledger_name = l.ledger_name AND le.company_id = l.company_id AND l.company_id = v.company_id
                WHERE v.company_id = %s AND l.group_code IN ({placeholders})
                AND v.date >= %s AND v.date <= %s
                AND v.voucher_type NOT IN ('Closing', 'Opening')
            """
            cursor.execute(query, params)
            result = cursor.fetchone()[0]
            return result or 0.0

        def get_net_movement_by_ledger_name_pattern(pattern):
             # Search across all expenses for depreciation
             query = """
                SELECT SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE -le.amount END)
                FROM ledger_entries le
                JOIN vouchers v ON le.voucher_number = v.voucher_number AND le.company_id = v.company_id
                JOIN ledgers l ON le.ledger_name = l.ledger_name AND le.company_id = l.company_id AND l.company_id = v.company_id
                JOIN groups g ON l.group_code = g.group_code AND g.company_id = v.company_id
                WHERE v.company_id = %s 
                AND (g.master_group_code IN ('MG008', 'MG009'))
                AND l.ledger_name LIKE %s
                AND v.date >= %s AND v.date <= %s
                AND v.voucher_type NOT IN ('Closing', 'Opening')
             """
             cursor.execute(query, (company_id, pattern, from_date, to_date))
             result = cursor.fetchone()[0]
             return result or 0.0

        # 1. Operating Activities
        
        # Net Profit before Tax
        # Income (MG006, MG007) - Expenses (MG008, MG009)
        income_movement = get_net_movement_by_mg(['MG006', 'MG007'])
        expense_movement = get_net_movement_by_mg(['MG008', 'MG009'])
        net_profit_before_tax = -(income_movement + expense_movement)
        
        # Adjustments
        # Depreciation
        depreciation = get_net_movement_by_ledger_name_pattern('%Depreciation%')
        # If depreciation is an expense (Debit), it returns positive.
        
        # Operating Profit before Working Capital Changes
        operating_profit = net_profit_before_tax + depreciation
        
        # Changes in Working Capital
        # Current Assets (MG001) excluding Cash (G005) and Bank (G006)
        # Increase (Dr) -> Outflow (Subtract)
        current_assets_change = get_net_movement_by_mg(['MG001'], exclude_group_codes=['G005', 'G006'])
        
        # Current Liabilities (MG003)
        # Increase (Cr) -> Inflow (Add) -> (Net Debit is negative, so -(-Val) = +Val)
        current_liabilities_change = get_net_movement_by_mg(['MG003'])
        
        # Breakdown for display
        # Use Standard Groups for breakdown: Inventory (G010), Receivables (G007), Payables (G008)
        inventory_movement = get_net_movement_by_group(['G010'])
        receivables_movement = get_net_movement_by_group(['G007'])
        payables_movement = get_net_movement_by_group(['G008'])
        
        # Cash Impact: -Movement
        inventory_change_cash = -inventory_movement
        receivables_change_cash = -receivables_movement
        payables_change_cash = -payables_movement
        
        # Calculate Other Liabilities Change (Cash Impact)
        # Total CL Change Cash Impact = -current_liabilities_change
        # Other Liab = Total CL - Payables
        other_liabilities_change_cash = (-current_liabilities_change) - payables_change_cash

        # Calculate Other Assets Change (Cash Impact)
        # Total CA Change Cash Impact = -current_assets_change
        # Other Assets = Total CA - Inventory - Receivables
        other_assets_change_cash = (-current_assets_change) - inventory_change_cash - receivables_change_cash

        # Total WC Changes
        total_wc_changes = -(current_assets_change + current_liabilities_change)
        
        cash_from_operations = operating_profit + total_wc_changes
        
        # 2. Investing Activities
        # Non-Current Assets (MG002)
        # Increase (Dr) -> Outflow.
        # We add back depreciation because we assume it was credited to the asset (or acc dep).
        # Net Debit = Purchase - Sale - Depreciation.
        # Cash Flow = -(Purchase - Sale) = -(Net Debit + Depreciation).
        fixed_assets_change = get_net_movement_by_mg(['MG002'])
        cash_from_investing = -(fixed_assets_change + depreciation)
        
        # 3. Financing Activities
        # Non-Current Liabilities (MG004) + Equity (MG005)
        # Increase (Cr) -> Inflow.
        financing_change = get_net_movement_by_mg(['MG004', 'MG005'])
        cash_from_financing = -financing_change
        
        net_change = cash_from_operations + cash_from_investing + cash_from_financing
        
        # 4. Opening and Closing Cash & Bank Balances
        # Cash: G005, Bank: G006
        def get_balance_at(date_str):
            # Optimized: If date is recent, use Reverse Calculation, else Forward.
            # Here we just use Forward for simplicity and correctness for "Opening"
            
            # Opening Balance + Movements up to date
            query = """
                SELECT 
                    SUM(l.opening_balance * CASE WHEN l.opening_balance_type='Debit' THEN 1 ELSE -1 END)
                FROM ledgers l
                WHERE l.company_id = %s AND l.group_code IN ('G005', 'G006')
            """
            cursor.execute(query, (company_id,))
            res = cursor.fetchone()
            opening_bal = res[0] if res and res[0] else 0.0
            
            query_move = """
                SELECT SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE -le.amount END)
                FROM ledger_entries le
                JOIN vouchers v ON le.voucher_number = v.voucher_number AND le.company_id = v.company_id
                JOIN ledgers l ON le.ledger_name = l.ledger_name AND le.company_id = l.company_id AND l.company_id = v.company_id
                WHERE v.company_id = %s AND l.group_code IN ('G005', 'G006')
                AND v.date <= %s
            """
            cursor.execute(query_move, (company_id, date_str))
            res_move = cursor.fetchone()
            movements = res_move[0] if res_move and res_move[0] else 0.0
            
            return opening_bal + movements

        # Calculate Opening Balance (as of from_date - 1 day)
        # However, "Opening Cash" for the period usually means Balance at Start of Day of from_date.
        # So we want Balance at (from_date - 1 day).
        from_date_obj = datetime.strptime(from_date, '%Y-%m-%d')
        prev_date = (from_date_obj - timedelta(days=1)).strftime('%Y-%m-%d')
        
        opening_cash_balance = get_balance_at(prev_date)
        closing_cash_balance = get_balance_at(to_date)
        
        return {
            'operating': cash_from_operations,
            'investing': cash_from_investing,
            'financing': cash_from_financing,
            'net_change': net_change,
            'opening_balance': opening_cash_balance,
            'closing_balance': closing_cash_balance,
            'details': {
                'net_profit': net_profit_before_tax,
                'depreciation': depreciation,
                'operating_profit': operating_profit,
                'wc_changes': total_wc_changes,
                'inventory_change': inventory_change_cash,
                'receivables_change': receivables_change_cash,
                'payables_change': payables_change_cash,
                'other_liabilities_change': other_liabilities_change_cash,
                'other_assets_change': other_assets_change_cash,
                'current_assets_change': -current_assets_change,
                'current_liabilities_change': -current_liabilities_change,
                'fixed_assets_change': cash_from_investing,
                'financing_change': cash_from_financing
            }
        }
        
    except Exception as e:
        print(f"Error in get_cash_flow_data: {e}")
        return {}
    finally:
        conn.close()

def get_vat_detailed_report_data(from_date=None, to_date=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return {'output_rows': [], 'input_rows': [], 'total_output_vat': 0, 'total_input_vat': 0}

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # 1. Identify all vouchers that have VAT entries (G011)
        # We fetch sum of Credit and Debit for G011 ledgers per voucher
        query = """
            SELECT 
                v.voucher_number,
                v.date,
                v.voucher_type,
                v.amount,
                v.narration,
                v.original_invoice_date,
                v.original_invoice_ref,
                SUM(CASE WHEN le.type='Credit' THEN le.amount ELSE 0 END) as vat_credit,
                SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE 0 END) as vat_debit
            FROM ledger_entries le
            JOIN vouchers v ON le.voucher_number = v.voucher_number AND le.company_id = v.company_id
            JOIN ledgers l ON le.ledger_name = l.ledger_name AND le.company_id = l.company_id AND l.company_id = v.company_id
            WHERE v.company_id = %s AND l.group_code = 'G011'
        """
        params = [company_id]
        if from_date:
            query += " AND v.date >= %s"
            params.append(from_date)
        if to_date:
            query += " AND v.date <= %s"
            params.append(to_date)
        
        query += " GROUP BY v.voucher_number, v.date, v.voucher_type, v.amount, v.narration, v.original_invoice_date, v.original_invoice_ref ORDER BY v.date, v.voucher_number"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        output_rows = []
        input_rows = []

        total_output_vat = 0.0
        total_input_vat = 0.0

        for vn, date, vtype, amount, narr, inv_date, inv_ref, v_credit, v_debit in rows:
            # Determine Party Name (N+1 query, but consistent with existing code)
            # Fetch the ledger that is NOT G011 and has the highest amount (likely the party)
            cursor.execute("""
                SELECT le.ledger_name 
                FROM ledger_entries le
                JOIN ledgers l ON le.ledger_name = l.ledger_name AND le.company_id = l.company_id
                WHERE l.company_id = %s AND le.voucher_number = %s 
                AND l.group_code NOT IN ('G011') 
                ORDER BY le.amount DESC LIMIT 1
            """, (company_id, vn))
            p_row = cursor.fetchone()
            party_name = p_row[0] if p_row else "Unknown"
        
            # Logic for Output vs Input
            # Sales/Income -> Output Section
            # Purchase/Expense -> Input Section
        
            # Classify based on Voucher Type
            if vtype in ['Sales', 'Sales Return', 'Service Income', 'Receipt']:
                # Output Section
                # Net VAT = Credit - Debit (Sales is Credit, Return is Debit)
                net_vat = v_credit - v_debit
        
                # Voucher Amount for Return is Positive in DB, but we want to show as Negative
                if vtype == 'Sales Return':
                    final_total = -amount
                else:
                    final_total = amount
        
                final_vat = net_vat
                final_taxable = final_total - final_vat
        
                output_rows.append({
                    'date': date,
                    'voucher_number': vn,
                    'voucher_type': vtype,
                    'invoice_date': inv_date,
                    'invoice_ref': inv_ref,
                    'party_name': party_name,
                    'taxable': final_taxable,
                    'vat': final_vat,
                    'total': final_total,
                    'narration': narr
                })
                total_output_vat += final_vat

            else:
                # Input Section (Purchase, Purchase Return, Payment, Journal, Expense)
                # Input VAT is Debit.
                # Net VAT = Debit - Credit (Purchase is Debit, Return is Credit)
                net_vat = v_debit - v_credit
        
                if vtype == 'Purchase Return':
                    final_total = -amount
                else:
                    final_total = amount
            
                final_vat = net_vat
                final_taxable = final_total - final_vat
        
                input_rows.append({
                    'date': date,
                    'voucher_number': vn,
                    'voucher_type': vtype,
                    'invoice_date': inv_date,
                    'invoice_ref': inv_ref,
                    'party_name': party_name,
                    'taxable': final_taxable,
                    'vat': final_vat,
                    'total': final_total,
                    'narration': narr
                })
                total_input_vat += final_vat
        
        return {
            'output_rows': output_rows,
            'input_rows': input_rows,
            'total_output_vat': total_output_vat,
            'total_input_vat': total_input_vat
        }

    except Exception as e:
        print(f"Error in get_vat_detailed_report_data: {e}")
        return {'output_rows': [], 'input_rows': [], 'total_output_vat': 0, 'total_input_vat': 0}
    finally:
        conn.close()


def get_stock_movement_data(item_name, from_date=None, to_date=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    try:
        running_qty = 0.0
        running_val = 0.0
        
        # 1. Initialize Start State
        if from_date:
            # Find last transaction before from_date
            cursor.execute("""
                SELECT running_qty, running_value 
                FROM item_entries ie
                JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
                WHERE v.company_id = %s AND ie.item_name = %s AND v.date < %s
                ORDER BY v.date DESC, v.voucher_id DESC, ie.id DESC
                LIMIT 1
            """, (company_id, item_name, from_date))
            row = cursor.fetchone()
            if row:
                running_qty = float(row[0] or 0)
                running_val = float(row[1] or 0)
            else:
                running_qty = 0.0
                running_val = 0.0
        else:
             # Start from inception
             running_qty = 0.0
             running_val = 0.0

        # 2. Get Movements WITHIN Range
        query = """
            SELECT v.voucher_number, v.date, v.voucher_type, ie.quantity, ie.unit_price, ie.type, ie.amount, ie.running_qty, ie.running_value,
                   COALESCE(ie.location_name, v.location_name, '')
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE v.company_id = %s AND ie.item_name = %s
        """
        params = [company_id, item_name]
        
        if from_date:
            query += " AND v.date >= %s"
            params.append(from_date)
        
        if to_date:
            query += " AND v.date <= %s"
            params.append(to_date)
            
        query += " ORDER BY v.date ASC, v.voucher_id ASC, ie.id ASC"
        
        cursor.execute(query, params)
        movements = cursor.fetchall()
        
        result = []
        
        for vn, dt, vt, qty, price, ietype, amt, r_qty, r_val, loc in movements:
             q = float(qty or 0)
             p = float(price or 0)
             
             # Stored values from DB
             current_running_qty = float(r_qty or 0)
             current_running_val = float(r_val or 0)
             
             # Determine In/Out for display
             is_in = False
             if vt in ('Purchase', 'Sales Return', 'Physical Stock', 'Opening'): is_in = True
             elif vt == 'Reversal' and ietype == 'Debit': is_in = True
             elif vt == 'Inventory Transfer' and ietype == 'Debit': is_in = True
             elif vt == 'Stock Adjustment' and ietype == 'Debit': is_in = True
             
             is_out = False
             if vt in ('Sales', 'Purchase Return'): is_out = True
             elif vt == 'Reversal' and ietype == 'Credit': is_out = True
             elif vt == 'Inventory Transfer' and ietype == 'Credit': is_out = True
             elif vt == 'Stock Adjustment' and ietype == 'Credit': is_out = True
             
             qty_in = q if is_in else 0.0
             qty_out = q if is_out else 0.0
             
             current_wap = (current_running_val / current_running_qty) if current_running_qty > 0 else 0.0
             
             result.append((
                 vn,
                 dt,
                 vt,
                 qty_in,
                 qty_out,
                 round(current_running_qty, 2),
                 round(current_wap, 2),
                 round(current_running_val, 2),
                 loc or ''
             ))
        
        print(f"get_stock_movement_data({item_name}): {len(result)} entries (Optimized)")
        return result

    except Exception as e:
        print(f"Error in get_stock_movement_data: {str(e)}")
        return []
    finally:
        conn.close()

def calculate_weighted_average_price(item_name, end_date=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return 0.0

    conn = get_connection()
    cursor = conn.cursor()
    try:
        query = """
            SELECT running_wap 
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE v.company_id = %s AND ie.item_name = %s
        """
        params = [company_id, item_name]
        
        if end_date:
            query += " AND v.date <= %s"
            params.append(end_date)
            
        query += " ORDER BY v.date DESC, v.voucher_id DESC, ie.id DESC LIMIT 1"
        
        cursor.execute(query, params)
        row = cursor.fetchone()
        if row and row[0] is not None:
            return float(row[0])
            
        # Fallback to opening price
        cursor.execute("SELECT opening_price FROM inventory WHERE company_id = %s AND name = %s", (company_id, item_name))
        row = cursor.fetchone()
        return float(row[0]) if row and row[0] else 0.0
    except Exception as e:
        print(f"Error in calculate_weighted_average_price: {str(e)}")
        return 0.0
    finally:
        conn.close()

def calculate_weighted_average_price_at(item_name, end_date, end_voucher_id, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return 0.0

    conn = get_connection()
    cursor = conn.cursor()
    try:
        query = """
            SELECT running_wap 
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE v.company_id = %s AND ie.item_name = %s AND (v.date < %s OR (v.date = %s AND v.voucher_id <= %s))
            ORDER BY v.date DESC, v.voucher_id DESC, ie.id DESC LIMIT 1
        """
        params = [company_id, item_name, end_date, end_date, end_voucher_id]
        
        cursor.execute(query, params)
        row = cursor.fetchone()
        if row and row[0] is not None:
            return float(row[0])
            
        cursor.execute("SELECT opening_price FROM inventory WHERE company_id = %s AND name = %s", (company_id, item_name))
        row = cursor.fetchone()
        return float(row[0]) if row and row[0] else 0.0
    except Exception:
        return 0.0
    finally:
        conn.close()

def replay_movements(item_name, start_date=None, end_date=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return 0, 0, 0, 0, 0, 0

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT opening_price, stock_quantity FROM inventory WHERE company_id = %s AND name = %s",
            (company_id, item_name)
        )
        row = cursor.fetchone() or (0, 0)
        opening_price = float(row[0] or 0)
        stock_qty_current = float(row[1] or 0)

        cursor.execute(
            """
            SELECT v.voucher_type, v.date as vdate, ie.quantity, ie.unit_price, ie.type
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE v.company_id = %s AND ie.item_name = %s
            ORDER BY v.date ASC, v.voucher_id ASC, ie.id ASC
            """,
            (company_id, item_name)
        )
        rows_all = cursor.fetchall()
        # Derive baseline opening quantity by removing all movements from current stock
        cursor.execute(
            """
            SELECT
                SUM(CASE WHEN v.voucher_type IN ('Purchase','Sales Return','Physical Stock') THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Reversal' AND ie.type = 'Debit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Inventory Transfer' AND ie.type = 'Debit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Stock Adjustment' AND ie.type = 'Debit' THEN ie.quantity ELSE 0 END) -
                SUM(CASE WHEN v.voucher_type IN ('Sales','Purchase Return') THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Inventory Transfer' AND ie.type = 'Credit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Stock Adjustment' AND ie.type = 'Credit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Reversal' AND ie.type = 'Credit' THEN ie.quantity ELSE 0 END)
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE v.company_id = %s AND ie.item_name = %s
            """,
            (company_id, item_name)
        )
        net_movements_all = float(cursor.fetchone()[0] or 0)
        opening_qty_baseline = stock_qty_current - net_movements_all
        if opening_qty_baseline < 0:
            opening_qty_baseline = 0.0

        current_qty = opening_qty_baseline
        current_val = opening_qty_baseline * opening_price

        def is_receipt(v_type, t):
            if v_type in ('Purchase', 'Sales Return', 'Physical Stock'):
                return True
            if v_type == 'Inventory Transfer' and (t or 'Debit') == 'Debit':
                return True
            if v_type == 'Stock Adjustment' and (t or 'Debit') == 'Debit':
                return True
            if v_type == 'Reversal' and (t or 'Debit') == 'Debit':
                return True
            return False

        def is_issue(v_type, t):
            if v_type in ('Sales', 'Purchase Return'):
                return True
            if v_type == 'Inventory Transfer' and (t or 'Debit') == 'Credit':
                return True
            if v_type == 'Stock Adjustment' and (t or 'Debit') == 'Credit':
                return True
            if v_type == 'Reversal' and (t or 'Debit') == 'Credit':
                return True
            return False

        opening_qty = current_qty
        opening_val = current_val
        receipts_val_period = 0.0

        for v_type, v_date, qty, rate, t in rows_all:
            qty = float(qty or 0)
            rate = float(rate or 0)
            wap_now = (current_val / current_qty) if current_qty > 0 else opening_price
            if start_date and v_date < start_date:
                if is_receipt(v_type, t):
                    val_in = qty * (wap_now if v_type in ('Inventory Transfer','Stock Adjustment','Sales Return') else rate)
                    current_qty += qty
                    current_val += val_in
                elif is_issue(v_type, t):
                    val_out = (qty * rate) if v_type == 'Purchase Return' else (((qty * (current_val / current_qty)) if current_qty > 0 else 0))
                    current_qty -= qty
                    current_val -= val_out
                opening_qty = current_qty
                opening_val = current_val
            elif end_date and v_date <= end_date or (not end_date):
                if is_receipt(v_type, t):
                    val_in = qty * (wap_now if v_type in ('Inventory Transfer','Stock Adjustment','Sales Return') else rate)
                    current_qty += qty
                    current_val += val_in
                    if not start_date or v_date >= start_date:
                        if v_type not in ('Sales Return','Inventory Transfer'):
                            receipts_val_period += val_in
                elif is_issue(v_type, t):
                    val_out = (qty * rate) if v_type == 'Purchase Return' else (((qty * (current_val / current_qty)) if current_qty > 0 else 0))
                    current_qty -= qty
                    current_val -= val_out
            if current_qty <= 0:
                current_qty = 0
                current_val = 0

        closing_qty = current_qty
        closing_val = current_val
        closing_wap = (closing_val / closing_qty) if closing_qty > 0 else 0
        return opening_qty, opening_val, receipts_val_period, closing_qty, closing_val, closing_wap
    except Exception:
        return 0, 0, 0, 0, 0, 0
    finally:
        conn.close()

def compute_period_costs(item_name, start_date=None, end_date=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return 0.0, 0.0

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT opening_price, stock_quantity FROM inventory WHERE company_id = %s AND name = %s",
            (company_id, item_name)
        )
        row = cursor.fetchone() or (0, 0)
        opening_price = float(row[0] or 0)
        stock_qty_current = float(row[1] or 0)

        cursor.execute(
            """
            SELECT v.voucher_type, v.date as pdate, ie.quantity, ie.unit_price, ie.type
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE v.company_id = %s AND ie.item_name = %s
            ORDER BY pdate, ie.id
            """,
            (company_id, item_name)
        )
        rows_all = cursor.fetchall()

        # Baseline opening quantity
        cursor.execute(
            """
            SELECT
                SUM(CASE WHEN v.voucher_type IN ('Purchase','Sales Return','Physical Stock') THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Reversal' AND ie.type = 'Debit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Inventory Transfer' AND ie.type = 'Debit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Stock Adjustment' AND ie.type = 'Debit' THEN ie.quantity ELSE 0 END) -
                SUM(CASE WHEN v.voucher_type IN ('Sales','Purchase Return') THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Inventory Transfer' AND ie.type = 'Credit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Stock Adjustment' AND ie.type = 'Credit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Reversal' AND ie.type = 'Credit' THEN ie.quantity ELSE 0 END)
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE v.company_id = %s AND ie.item_name = %s
            """,
            (company_id, item_name)
        )
        net_movements_all = float(cursor.fetchone()[0] or 0)
        opening_qty_baseline = stock_qty_current - net_movements_all
        if opening_qty_baseline < 0:
            opening_qty_baseline = 0.0

        current_qty = opening_qty_baseline
        current_val = opening_qty_baseline * opening_price

        def is_receipt(v_type, t):
            if v_type in ('Purchase', 'Sales Return', 'Physical Stock'):
                return True
            if v_type == 'Inventory Transfer' and (t or 'Debit') == 'Debit':
                return True
            if v_type == 'Stock Adjustment' and (t or 'Debit') == 'Debit':
                return True
            if v_type == 'Reversal' and (t or 'Debit') == 'Debit':
                return True
            return False

        def is_issue(v_type, t):
            if v_type in ('Sales', 'Purchase Return'):
                return True
            if v_type == 'Inventory Transfer' and (t or 'Debit') == 'Credit':
                return True
            if v_type == 'Stock Adjustment' and (t or 'Debit') == 'Credit':
                return True
            if v_type == 'Reversal' and (t or 'Debit') == 'Credit':
                return True
            return False

        issues_val_period = 0.0
        returns_val_period = 0.0

        for v_type, v_date, qty, rate, t in rows_all:
            qty = float(qty or 0)
            rate = float(rate or 0)
            # Current WAP before applying this movement
            wap_now = (current_val / current_qty) if current_qty > 0 else opening_price

            if start_date and v_date < start_date:
                # Pre-period movements affect the pool only
                if is_receipt(v_type, t):
                    val_in = round(qty * (rate if v_type != 'Sales Return' else (wap_now)), 2)
                    current_qty += qty
                    current_val += val_in
                elif is_issue(v_type, t):
                    val_out = round((qty * rate) if v_type == 'Purchase Return' else (qty * wap_now), 2)
                    current_qty -= qty
                    current_val -= val_out
            else:
                if is_receipt(v_type, t):
                    # Accumulate returns separately at current WAP
                    if v_type == 'Sales Return' and (not end_date or v_date <= end_date):
                        returns_val_period += round(qty * wap_now, 2)
                    val_in = round(qty * (rate if v_type != 'Sales Return' else (wap_now)), 2)
                    current_qty += qty
                    current_val += val_in
                elif is_issue(v_type, t):
                    if v_type == 'Sales' and (not end_date or v_date <= end_date):
                        issues_val_period += round(qty * wap_now, 2)
                    val_out = round((qty * rate) if v_type == 'Purchase Return' else (qty * wap_now), 2)
                    current_qty -= qty
                    current_val -= val_out

            if current_qty <= 0:
                current_qty = 0
                current_val = 0

        return round(issues_val_period, 2), round(returns_val_period, 2)
    except Exception:
        return 0.0, 0.0
    finally:
        conn.close()
def replay_movements_by_location(item_name, start_date=None, end_date=None, company_id=None, allow_negative=False):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT opening_price, stock_quantity, COALESCE(opening_location_name, 'Main Location') FROM inventory WHERE company_id = %s AND name = %s",
            (company_id, item_name)
        )
        row = cursor.fetchone() or (0, 0, 'Main Location')
        opening_price = float(row[0] or 0)
        stock_qty_current = float(row[1] or 0)
        opening_loc = row[2] or 'Main Location'

        # Derive baseline opening quantity
        cursor.execute(
            """
            SELECT
                SUM(CASE WHEN v.voucher_type IN ('Purchase','Sales Return','Physical Stock') THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Reversal' AND ie.type = 'Debit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Inventory Transfer' AND ie.type = 'Debit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Stock Adjustment' AND ie.type = 'Debit' THEN ie.quantity ELSE 0 END) -
                SUM(CASE WHEN v.voucher_type IN ('Sales','Purchase Return') THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Inventory Transfer' AND ie.type = 'Credit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Stock Adjustment' AND ie.type = 'Credit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Reversal' AND ie.type = 'Credit' THEN ie.quantity ELSE 0 END)
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE v.company_id = %s AND ie.item_name = %s
            """,
            (company_id, item_name)
        )
        net_movements_all = float(cursor.fetchone()[0] or 0)
        opening_qty_baseline = stock_qty_current - net_movements_all
        if opening_qty_baseline < 0:
            opening_qty_baseline = 0.0

        pools = {}
        if opening_qty_baseline > 0:
            pools[opening_loc] = opening_qty_baseline

        cursor.execute(
            """
            SELECT v.voucher_type, v.date as vdate, ie.quantity, ie.unit_price, ie.type, COALESCE(ie.location_name, 'Main Location')
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE v.company_id = %s AND ie.item_name = %s
            ORDER BY v.date ASC, v.voucher_id ASC, ie.id ASC
            """,
            (company_id, item_name)
        )
        rows_all = cursor.fetchall()

        def is_receipt(v_type, t):
            if v_type in ('Purchase', 'Sales Return', 'Physical Stock'):
                return True
            if v_type == 'Inventory Transfer' and (t or 'Debit') == 'Debit':
                return True
            if v_type == 'Stock Adjustment' and (t or 'Debit') == 'Debit':
                return True
            if v_type == 'Reversal' and (t or 'Debit') == 'Debit':
                return True
            return False

        def is_issue(v_type, t):
            if v_type in ('Sales', 'Purchase Return'):
                return True
            if v_type == 'Inventory Transfer' and (t or 'Debit') == 'Credit':
                return True
            if v_type == 'Stock Adjustment' and (t or 'Debit') == 'Credit':
                return True
            if v_type == 'Reversal' and (t or 'Debit') == 'Credit':
                return True
            return False

        for v_type, v_date, qty, rate, t, loc in rows_all:
            if end_date and v_date > end_date:
                continue
            qty = float(qty or 0)
            
            if loc not in pools:
                pools[loc] = 0.0
            
            if is_receipt(v_type, t):
                pools[loc] += qty
            elif is_issue(v_type, t):
                pools[loc] -= qty
            
            if pools[loc] < 0 and not allow_negative:
                pools[loc] = 0.0

        # Calculate Global WAP at the end_date (or current if None)
        global_wap = calculate_weighted_average_price(item_name, end_date, company_id)

        results = []
        for loc, q in pools.items():
            v = q * global_wap
            results.append((loc, q, v, global_wap))
        return results
    except Exception:
        return []
    finally:
        conn.close()

def get_inventory_stock(item_name, start_date=None, end_date=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return 0, 0, 0

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT opening_price, stock_quantity FROM inventory WHERE company_id = %s AND name = %s",
            (company_id, item_name)
        )
        inv_row = cursor.fetchone() or (0, 0)
        inv_opening_price = inv_row[0] or 0
        inv_opening_qty = inv_row[1] or 0

        opening_query = """
            SELECT
                SUM(CASE WHEN v.voucher_type IN ('Purchase', 'Sales Return', 'Physical Stock') THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Reversal' AND ie.type = 'Debit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Inventory Transfer' AND ie.type = 'Debit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Stock Adjustment' AND ie.type = 'Debit' THEN ie.quantity ELSE 0 END) -
                SUM(CASE WHEN v.voucher_type IN ('Sales', 'Purchase Return') THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Inventory Transfer' AND ie.type = 'Credit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Stock Adjustment' AND ie.type = 'Credit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Reversal' AND ie.type = 'Credit' THEN ie.quantity ELSE 0 END
                )
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE v.company_id = %s AND ie.item_name = %s AND v.date < %s
        """
        opening_params = [company_id, item_name, start_date] if start_date else [company_id, item_name, '9999-12-31']
        cursor.execute(opening_query, opening_params)
        opening_qty_movements = cursor.fetchone()[0] or 0

        if start_date:
            opening_qty = (inv_opening_qty or 0) + (opening_qty_movements or 0)
            opening_wap = calculate_weighted_average_price(item_name, start_date, company_id)
        else:
            opening_qty = inv_opening_qty or 0
            opening_wap = inv_opening_price or 0

        opening_stock_value = opening_qty * opening_wap if opening_qty > 0 else 0
        
        purchase_query = """
            SELECT SUM(
                CASE
                    WHEN v.voucher_type = 'Purchase' THEN ie.amount
                    WHEN v.voucher_type = 'Purchase Return' THEN -ie.amount
                    ELSE 0
                END
            )
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE v.company_id = %s AND ie.item_name = %s
        """
        purchase_params = [company_id, item_name]
        
        if start_date:
            purchase_query += " AND v.date >= %s"
            purchase_params.append(start_date)
        if end_date:
            purchase_query += " AND v.date <= %s"
            purchase_params.append(end_date)
        
        cursor.execute(purchase_query, purchase_params)
        purchases = cursor.fetchone()[0] or 0
        
        period_qty_query = """
            SELECT
                SUM(CASE WHEN v.voucher_type IN ('Purchase', 'Sales Return', 'Physical Stock') THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Reversal' AND ie.type = 'Debit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Inventory Transfer' AND ie.type = 'Debit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Stock Adjustment' AND ie.type = 'Debit' THEN ie.quantity ELSE 0 END) -
                SUM(CASE WHEN v.voucher_type IN ('Sales', 'Purchase Return') THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Inventory Transfer' AND ie.type = 'Credit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Stock Adjustment' AND ie.type = 'Credit' THEN ie.quantity ELSE 0 END
                    + CASE WHEN v.voucher_type = 'Reversal' AND ie.type = 'Credit' THEN ie.quantity ELSE 0 END
                )
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE v.company_id = %s AND ie.item_name = %s AND v.date >= %s
        """

        period_params = [company_id, item_name, start_date or '0001-01-01']
        if end_date:
            period_qty_query += " AND v.date <= %s"
            period_params.append(end_date)
        cursor.execute(period_qty_query, period_params)
        period_qty_net = cursor.fetchone()[0] or 0
        closing_qty = (opening_qty or 0) + (period_qty_net or 0)
        
        closing_wap = calculate_weighted_average_price(item_name, end_date, company_id)
        closing_stock_value = closing_qty * closing_wap if closing_qty > 0 else 0
        
        print(f"get_inventory_stock({item_name}, {start_date}, {end_date}): opening={opening_stock_value}, purchases={purchases}, closing={closing_stock_value}")
        return opening_stock_value, purchases, closing_stock_value
    except Exception as e:
        print(f"Error in get_inventory_stock: {str(e)}")
        return 0, 0, 0
    finally:
        conn.close()

def get_balance_sheet_data(as_of_date=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return {'assets': {}, 'liabilities': {}}, 0, 0

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Optimization: Use stored balances if date is current
        if is_latest_date(cursor, as_of_date, company_id):
            print(f"get_balance_sheet_data({as_of_date}): Using optimized stored balances.")
            cursor.execute("""
                SELECT l.ledger_name, g.group_name, g.nature, l.closing_balance
                FROM ledgers l
                JOIN groups g ON l.group_code = g.group_code AND l.company_id = g.company_id
                WHERE l.company_id = %s
            """, (company_id,))
            ledgers = cursor.fetchall()
            
            assets = {}
            liabilities = {}
            total_assets = 0
            total_liabilities = 0
            
            for ledger_name, group_name, nature, closing_balance in ledgers:
                if closing_balance != 0:
                    if nature == "Assets":
                        if group_name not in assets:
                            assets[group_name] = []
                        rounded_amount = round(closing_balance, 2)
                        assets[group_name].append({'ledger_name': ledger_name, 'amount': rounded_amount})
                        total_assets += closing_balance
                    elif nature == "Liabilities":
                        if group_name not in liabilities:
                            liabilities[group_name] = []
                        rounded_amount = round(-closing_balance, 2)
                        liabilities[group_name].append({'ledger_name': ledger_name, 'amount': rounded_amount})
                        total_liabilities += -closing_balance
            
            # For Net Profit, we still need to calculate it because it's not a stored ledger balance
            # But we can pass None as to_date to imply "latest" which get_profit_and_loss_data might optimize later if needed
            _, total_income, total_expenses, net_profit = get_profit_and_loss_data(None, as_of_date, company_id)

            if net_profit != 0:
                if 'Capital Account' not in liabilities:
                    liabilities['Capital Account'] = []
                rounded_np = round(net_profit, 2)
                liabilities['Capital Account'].append({'ledger_name': 'Net Profit/Loss', 'amount': rounded_np})
                total_liabilities += net_profit

            balance_sheet = {'assets': assets, 'liabilities': liabilities}
            print(f"get_balance_sheet_data({as_of_date}): total_assets={total_assets}, total_liabilities={total_liabilities}")
            return balance_sheet, round(total_assets, 2), round(total_liabilities, 2)

        # Optimization: Reverse Calculation for Backdated Reports
        print(f"get_balance_sheet_data({as_of_date}): Using Reverse Calculation.")
        
        # 1. Get Current Balances
        cursor.execute("""
            SELECT l.ledger_name, g.group_name, g.nature, l.closing_balance
            FROM ledgers l
            JOIN groups g ON l.group_code = g.group_code AND l.company_id = g.company_id
            WHERE l.company_id = %s
        """, (company_id,))
        ledgers = cursor.fetchall()
        
        # 2. Get Future Movements (Net)
        cursor.execute("""
            SELECT le.ledger_name, SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE -le.amount END)
            FROM ledger_entries le
            JOIN vouchers v ON le.voucher_number = v.voucher_number AND le.company_id = v.company_id
            WHERE v.company_id = %s AND (v.date > %s OR (v.date = %s AND v.voucher_type = 'Closing'))
            GROUP BY le.ledger_name
        """, (company_id, as_of_date, as_of_date))
        future_movements = dict(cursor.fetchall())
        
        assets = {}
        liabilities = {}
        total_assets = 0
        total_liabilities = 0
        
        for ledger_name, group_name, nature, current_bal in ledgers:
            future_change = future_movements.get(ledger_name, 0.0)
            # Historical = Current - Future
            hist_bal = (current_bal or 0.0) - future_change
            
            if abs(hist_bal) > 0.001:
                if nature == "Assets":
                    if group_name not in assets:
                        assets[group_name] = []
                    rounded_amount = round(hist_bal, 2)
                    assets[group_name].append({'ledger_name': ledger_name, 'amount': rounded_amount})
                    total_assets += hist_bal
                elif nature == "Liabilities":
                    if group_name not in liabilities:
                        liabilities[group_name] = []
                    # Liability Balance is Credit (Negative). Show as Positive.
                    rounded_amount = round(-hist_bal, 2)
                    liabilities[group_name].append({'ledger_name': ledger_name, 'amount': rounded_amount})
                    total_liabilities += -hist_bal
        
        # For Net Profit
        _, total_income, total_expenses, net_profit = get_profit_and_loss_data(None, as_of_date, company_id)
        
        if net_profit != 0:
            if 'Capital Account' not in liabilities:
                liabilities['Capital Account'] = []
            rounded_np = round(net_profit, 2)
            liabilities['Capital Account'].append({'ledger_name': 'Net Profit/Loss', 'amount': rounded_np})
            total_liabilities += net_profit
        

        balance_sheet = {'assets': assets, 'liabilities': liabilities}
        return balance_sheet, round(total_assets, 2), round(total_liabilities, 2)
    except Exception as e:
        print(f"Error in get_balance_sheet_data: {str(e)}")
        return {'assets': {}, 'liabilities': {}}, 0, 0
    finally:
        conn.close()

def get_profit_and_loss_data(from_date=None, to_date=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return {'income': {}, 'expenses': {}}, 0, 0, 0

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT l.ledger_name, g.group_name, g.nature
            FROM ledgers l
            JOIN groups g ON l.group_code = g.group_code AND l.company_id = g.company_id
            WHERE l.company_id = %s
            AND g.nature IN ('Income', 'Expenses')
            AND g.group_name != 'Purchase'
        """, (company_id,))
        ledgers_list = cursor.fetchall()
        
        cursor.execute("SELECT name FROM inventory WHERE company_id = %s", (company_id,))
        items = cursor.fetchall()
        
        income = {}
        expenses = {}
        total_income = 0
        total_expenses = 0
        
        # Optimization: If from_date is None (Inception to Date), use Reverse Calculation or Stored Balances
        if from_date is None:
            ledger_balances = {}
            if is_latest_date(cursor, to_date, company_id):
                print(f"get_profit_and_loss_data(None, {to_date}): Using optimized stored balances.")
                cursor.execute("""
                    SELECT l.ledger_name, g.group_name, g.nature, l.closing_balance
                    FROM ledgers l
                    JOIN groups g ON l.group_code = g.group_code AND l.company_id = g.company_id
                    WHERE l.company_id = %s
                    AND g.nature IN ('Income', 'Expenses')
                    AND g.group_name != 'Purchase'
                """, (company_id,))
                rows = cursor.fetchall()
                ledger_balances = {row[0]: (row[1], row[2], row[3]) for row in rows}
            else:
                print(f"get_profit_and_loss_data(None, {to_date}): Using Reverse Calculation.")
                # Current Balances
                cursor.execute("""
                    SELECT l.ledger_name, g.group_name, g.nature, l.closing_balance
                    FROM ledgers l
                    JOIN groups g ON l.group_code = g.group_code AND l.company_id = g.company_id
                    WHERE l.company_id = %s
                    AND g.nature IN ('Income', 'Expenses')
                    AND g.group_name != 'Purchase'
                """, (company_id,))
                current_rows = cursor.fetchall()
                
                # Future Movements
                cursor.execute("""
                    SELECT le.ledger_name, SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE -le.amount END)
                    FROM ledger_entries le
                    JOIN vouchers v ON le.voucher_number = v.voucher_number AND le.company_id = v.company_id
                    WHERE v.company_id = %s AND (v.date > %s OR (v.date = %s AND v.voucher_type = 'Closing'))
                    GROUP BY le.ledger_name
                """, (company_id, to_date, to_date,))
                future_movements = dict(cursor.fetchall())
                
                for name, group, nature, curr_bal in current_rows:
                    fut = future_movements.get(name, 0.0)
                    hist_bal = (curr_bal or 0.0) - fut
                    ledger_balances[name] = (group, nature, hist_bal)
            
            # Populate result dicts from optimized balances
            for ledger_name, (group_name, nature, balance) in ledger_balances.items():
                amount = 0
                if nature == "Income":
                    # Income has Credit balance (Negative in ledgers table convention usually, but let's verify)
                    # In ledgers table: Debit +, Credit -. 
                    # Income is Credit nature, so balance should be negative.
                    # We want positive amount for P&L display.
                    amount = -balance
                else: # Expenses
                    # Expense is Debit nature, balance positive.
                    amount = balance
                    
                if abs(amount) > 0.001:
                    if nature == "Income":
                        if group_name not in income: income[group_name] = []
                        income[group_name].append({'ledger_name': ledger_name, 'amount': round(amount, 2)})
                        total_income += amount
                    elif nature == "Expenses":
                        if group_name not in expenses: expenses[group_name] = []
                        expenses[group_name].append({'ledger_name': ledger_name, 'amount': round(amount, 2)})
                        total_expenses += amount

            # Check if COGS needs to be calculated dynamically (if ledger is empty or missing)
            # This mimics the logic in the non-optimized path
            has_cogs_ledger = False
            if 'Cost of Goods Sold' in expenses:
                # Check if it has any non-zero entries from the ledger balance check above
                # Actually, the loop above adds it if balance > 0.001
                # If it's here, we assume the ledger balance covers it.
                # But wait! The non-optimized path CHECKS if the sum is 0, and if so, CALCULATES it.
                # Here, if the ledger balance is 0, it won't be in 'expenses'.
                # So we just need to check if we have a significant COGS value.
                pass

            # We need to check the ACTUAL ledger balance of COGS to decide if we should calculate it.
            # We already have 'ledger_balances'.
            cogs_balance = 0.0
            if 'Cost of Goods Sold' in ledger_balances:
                # ledger_balances[name] = (group, nature, hist_bal)
                cogs_balance = ledger_balances['Cost of Goods Sold'][2]
            
            if abs(cogs_balance) < 0.01:
                 # Ledger is empty/near-zero, so we must calculate COGS dynamically
                 # For the "Inception to Date" case (from_date is None), we can use 
                 # compute_period_costs(item, None, to_date).
                 
                 if 'Cost of Goods Sold' not in expenses:
                     expenses['Cost of Goods Sold'] = []
                     
                 cursor.execute("SELECT name FROM inventory WHERE company_id = %s", (company_id,))
                 items = cursor.fetchall()
                 cogs_total_calc = 0.0
                 
                 # Determine start date for COGS calculation
                 cogs_start_date = None
                 if from_date is None:
                     # Check for last closing date to avoid recalculating closed COGS
                     # This ensures we align with the closed/open state of the Sales ledger
                     cursor.execute("SELECT MAX(date) FROM vouchers WHERE company_id = %s AND voucher_type = 'Closing'", (company_id,))
                     last_closing_row = cursor.fetchone()
                     last_closing = last_closing_row[0] if last_closing_row else None
                     
                     if last_closing:
                         try:
                             last_closing_dt = datetime.strptime(last_closing, "%Y-%m-%d")
                             cogs_start_date = (last_closing_dt + timedelta(days=1)).strftime("%Y-%m-%d")
                         except ValueError:
                             pass

                 for (iname,) in items:
                     # optimization: compute_period_costs might be slow if called for every item repeatedly.
                     # But this is what the non-optimized path does.
                     issues_val, returns_val = compute_period_costs(iname, cogs_start_date, to_date, company_id)
                     cogs_item = issues_val - returns_val
                     
                     if cogs_item != 0:
                         expenses['Cost of Goods Sold'].append({'ledger_name': f"COGS - {iname}", 'amount': round(cogs_item, 2)})
                         total_expenses += cogs_item
                         cogs_total_calc += cogs_item

        else:
            # Original Logic for Period (from_date != None) - Summing entries
            for ledger_name, group_name, nature in ledgers_list:
                query_ledger = """
                    SELECT SUM(CASE WHEN le.type = 'Debit' THEN le.amount ELSE 0 END) as debit,
                           SUM(CASE WHEN le.type = 'Credit' THEN le.amount ELSE 0 END) as credit
                    FROM ledger_entries le
                    JOIN vouchers v ON le.voucher_number = v.voucher_number AND le.company_id = v.company_id
                    WHERE v.company_id = %s AND le.ledger_name = %s AND v.voucher_type != 'Closing'
                """
                
                params = [company_id, ledger_name]
                
                if from_date:
                    query_ledger += " AND v.date >= %s"
                    params.append(from_date)
                if to_date:
                    query_ledger += " AND v.date <= %s"
                    params.append(to_date)
                
                cursor.execute(query_ledger, params)
                ledger_debit, ledger_credit = cursor.fetchone()
                
                total_debit = (ledger_debit or 0)
                total_credit = (ledger_credit or 0)
                
                amount = total_credit - total_debit if nature == "Income" else total_debit - total_credit
                
                if amount != 0:
                    if nature == "Income":
                        if group_name not in income:
                            income[group_name] = []
                        income[group_name].append({'ledger_name': ledger_name, 'amount': round(amount, 2)})
                        total_income += amount
                    elif nature == "Expenses":
                        if group_name not in expenses:
                            expenses[group_name] = []
                        expenses[group_name].append({'ledger_name': ledger_name, 'amount': round(amount, 2)})
                        total_expenses += amount
        
        # COGS Calculation (Preserved)
        if 'Cost of Goods Sold' not in expenses:
            expenses['Cost of Goods Sold'] = []
        query_cogs_ledger_sum = (
            "SELECT SUM(CASE WHEN le.type = 'Debit' THEN le.amount ELSE -le.amount END) "
            "FROM ledger_entries le JOIN vouchers v ON le.voucher_number = v.voucher_number AND le.company_id = v.company_id "
            "WHERE v.company_id = %s AND le.ledger_name = 'Cost of Goods Sold' AND v.voucher_type != 'Closing'"
        )
        params_cogs = [company_id]
        if from_date:
            query_cogs_ledger_sum += " AND v.date >= %s"
            params_cogs.append(from_date)
        if to_date:
            query_cogs_ledger_sum += " AND v.date <= %s"
            params_cogs.append(to_date)
        cursor.execute(query_cogs_ledger_sum, params_cogs)
        cogs_ledger_sum = float(cursor.fetchone()[0] or 0)
        if abs(cogs_ledger_sum) < 0.01:
            cursor.execute("SELECT name FROM inventory WHERE company_id = %s", (company_id,))
            items = cursor.fetchall()
            cogs_total = 0.0
            for (iname,) in items:
                issues_val, returns_val = compute_period_costs(iname, from_date, to_date, company_id)
                cogs_item = issues_val - returns_val
                if cogs_item != 0:
                    expenses['Cost of Goods Sold'].append({'ledger_name': f"COGS - {iname}", 'amount': round(cogs_item, 2)})
                    total_expenses += cogs_item
                    cogs_total += cogs_item
        
        net_profit = total_income - total_expenses
        profit_and_loss = {'income': income, 'expenses': expenses}
        
        print(f"get_profit_and_loss_data({from_date}, {to_date}): net_profit={net_profit}")
        return profit_and_loss, round(total_income, 2), round(total_expenses, 2), net_profit
    except Exception as e:
        print(f"Error in get_profit_and_loss_data: {str(e)}")
        return {'income': {}, 'expenses': {}}, 0, 0, 0
    finally:
        conn.close()

def get_item_closing_stock(item_name, as_of_date=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return 0.0, 0.0

    as_of_date_str = to_date_str(as_of_date)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Optimization: Use stored inventory values if date is current
        if is_latest_date(cursor, as_of_date_str, company_id):
            cursor.execute("""
                SELECT stock_quantity, stock_value
                FROM inventory
                WHERE company_id = %s AND name = %s
            """, (company_id, item_name))
            row = cursor.fetchone()
            if row:
                return float(row[0] or 0.0), float(row[1] or 0.0)
            return 0.0, 0.0

        # Optimization: Use stored running values (Snapshot) for Backdated Reports
        cursor.execute("""
            SELECT ie.running_qty, ie.running_value
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
            WHERE v.company_id = %s AND ie.item_name = %s AND v.date <= %s
            ORDER BY v.date DESC, v.voucher_id DESC, ie.id DESC
            LIMIT 1
        """, (company_id, item_name, as_of_date_str))
        
        row = cursor.fetchone()
        if row:
            return float(row[0] or 0.0), float(row[1] or 0.0)
        return 0.0, 0.0
    finally:
        conn.close()

def get_closing_inventory_data(as_of_date=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return [], 0.0

    as_of_date_str = to_date_str(as_of_date)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Location breakdown: when the company uses multiple locations, replay
        # each item's movements per location so the report shows location-wise
        # closing stock instead of a single 'All Locations' row.
        multiple_locations = False
        try:
            cursor.execute(
                "SELECT multiple_locations_applicable FROM company_settings WHERE company_id = %s",
                (company_id,)
            )
            row_ml = cursor.fetchone()
            multiple_locations = bool(row_ml and row_ml[0])
        except Exception as e:
            print(f"get_closing_inventory_data: could not read location setting: {e}")

        if multiple_locations:
            print(f"get_closing_inventory_data({as_of_date_str}): Using per-location replay.")
            cursor.execute("""
                SELECT i.item_code, i.name, COALESCE(ig.group_name, '')
                FROM inventory i
                LEFT JOIN inventory_groups ig ON i.stock_group_code = ig.group_code AND i.company_id = ig.company_id
                WHERE i.company_id = %s
                ORDER BY i.name
            """, (company_id,))
            inventory_items = cursor.fetchall()

            closing_inventory = []
            total_cost_amount = 0.0
            for item_code, item_name, group_name in inventory_items:
                per_loc = replay_movements_by_location(
                    item_name, end_date=as_of_date_str, company_id=company_id,
                    allow_negative=True  # must reconcile with Balance Sheet, incl. negative stock
                )
                non_zero = [(loc, q, v, wap) for (loc, q, v, wap) in (per_loc or []) if round(q, 2) != 0]
                if not non_zero:
                    # Item with no stock at any location — single zero row
                    closing_inventory.append({
                        'item_code': item_code,
                        'item_name': item_name,
                        'group_name': group_name,
                        'location_name': '',
                        'quantity': 0.0,
                        'wap': 0.0,
                        'cost_amount': 0.0,
                    })
                    continue
                for loc, q, v, wap in non_zero:
                    closing_inventory.append({
                        'item_code': item_code,
                        'item_name': item_name,
                        'group_name': group_name,
                        'location_name': loc,
                        'quantity': round(q, 2),
                        'wap': round(wap, 2),
                        'cost_amount': round(v, 2),
                    })
                    total_cost_amount += round(v, 2)
            return closing_inventory, round(total_cost_amount, 2)

        # Optimization: Use stored inventory values if date is current
        if is_latest_date(cursor, as_of_date_str, company_id):
            print(f"get_closing_inventory_data({as_of_date_str}): Using optimized stored values.")
            cursor.execute("""
                SELECT i.item_code, i.name, COALESCE(ig.group_name, ''), i.stock_quantity, i.stock_value
                FROM inventory i
                LEFT JOIN inventory_groups ig ON i.stock_group_code = ig.group_code AND i.company_id = ig.company_id
                WHERE i.company_id = %s
            """, (company_id,))
            inventory_items = cursor.fetchall()
            closing_inventory = []
            total_cost_amount = 0.0

            for item_code, item_name, group_name, stock_quantity, stock_value in inventory_items:
                q = stock_quantity or 0.0
                v = stock_value or 0.0
                wap = (v / q) if q != 0 else 0
                closing_inventory.append({
                    'item_code': item_code,
                    'item_name': item_name,
                    'group_name': group_name,
                    'location_name': 'Main Location',
                    'quantity': round(q, 2),
                    'wap': round(wap, 2),
                    'cost_amount': round(v, 2),
                })
                total_cost_amount += round(v, 2)

            return closing_inventory, round(total_cost_amount, 2)

        # Optimization: Use stored running values (Snapshot) for Backdated Reports
        # This avoids replaying full history.
        print(f"get_closing_inventory_data({as_of_date}): Using Snapshot Optimization (Global/All Locations).")

        cursor.execute("""
            SELECT i.item_code, i.name, COALESCE(ig.group_name, '')
            FROM inventory i
            LEFT JOIN inventory_groups ig ON i.stock_group_code = ig.group_code AND i.company_id = ig.company_id
            WHERE i.company_id = %s
        """, (company_id,))
        inventory_items = cursor.fetchall()
        closing_inventory = []
        total_cost_amount = 0.0
        for item_code, item_name, group_name in inventory_items:
            # Find the last entry on or before as_of_date
            # We need running_qty and running_value
            cursor.execute("""
                SELECT ie.running_qty, ie.running_value
                FROM item_entries ie
                JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
                WHERE v.company_id = %s AND ie.item_name = %s AND v.date <= %s
                ORDER BY v.date DESC, v.voucher_id DESC, ie.id DESC
                LIMIT 1
            """, (company_id, item_name, as_of_date_str))
            
            row = cursor.fetchone()
            if row:
                q = row[0] or 0.0
                v = row[1] or 0.0
            else:
                # No entries before this date -> 0
                q = 0.0
                v = 0.0
            
            wap = (v / q) if q != 0 else 0
            
            closing_inventory.append({
                'item_code': item_code,
                'item_name': item_name,
                'group_name': group_name,
                'location_name': 'Main Location', # Single-location company (backdated snapshot)
                'quantity': round(q, 2),
                'wap': round(wap, 2),
                'cost_amount': round(v, 2),
            })
            total_cost_amount += round(v, 2)
        return closing_inventory, round(total_cost_amount, 2)
    except Exception as e:
        print(f"Error in get_closing_inventory_data: {str(e)}")
        return [], 0
    finally:
        conn.close()
