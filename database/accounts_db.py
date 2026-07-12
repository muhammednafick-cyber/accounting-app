"""
Accounts module: Groups, Ledgers, Cost Centers
"""
# import sqlite3 - removed
from .config import get_connection
from .company_db import get_current_company_id

def ensure_default_master_groups(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return

    conn = get_connection()
    cursor = conn.cursor()
    
    # Default Master Groups
    # (code, name, nature)
    default_master_groups = [
        ('MG001', 'Current Assets', 'Assets'),
        ('MG002', 'Non-Current Assets', 'Assets'),
        ('MG003', 'Current Liabilities', 'Liabilities'),
        ('MG004', 'Non-Current Liabilities', 'Liabilities'),
        ('MG005', 'Equity', 'Liabilities'),
        ('MG006', 'Direct Income', 'Income'),
        ('MG007', 'Indirect Income', 'Income'),
        ('MG008', 'Direct Expenses', 'Expenses'),
        ('MG009', 'Indirect Expenses', 'Expenses'),
    ]
    
    data_to_insert = [(company_id, code, name, nature) for code, name, nature in default_master_groups]
    
    cursor.executemany(
        "INSERT INTO master_groups (company_id, master_group_code, master_group_name, nature) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
        data_to_insert
    )
    conn.commit()
    conn.close()

def ensure_default_groups(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return

    ensure_default_master_groups(company_id)

    conn = get_connection()
    cursor = conn.cursor()
    # (code, name, nature, master_group_code)
    default_groups = [
        ('G001', 'Sales', 'Income', 'MG006'), # Direct Income
        ('G002', 'Purchase', 'Expenses', 'MG008'), # Direct Expenses
        ('G003', 'Fixed Assets', 'Assets', 'MG002'), # Non-Current Assets
        ('G005', 'Cash Accounts', 'Assets', 'MG001'), # Current Assets
        ('G006', 'Bank Accounts', 'Assets', 'MG001'), # Current Assets
        ('G007', 'Debtors', 'Assets', 'MG001'), # Current Assets
        ('G008', 'Creditors', 'Liabilities', 'MG003'), # Current Liabilities
        ('G009', 'Capital Account', 'Liabilities', 'MG005'), # Equity
        ('G010', 'Inventory', 'Assets', 'MG001'), # Current Assets
        ('G011', 'Duties And Taxes', 'Liabilities', 'MG003'), # Current Liabilities
        ('G012', 'Direct Expenses', 'Expenses', 'MG008'), # Direct Expenses
        ('G013', 'Indirect Expenses', 'Expenses', 'MG009'), # Indirect Expenses
        ('G014', 'Direct Income', 'Income', 'MG006'), # Direct Income
        ('G015', 'Indirect Income', 'Income', 'MG007'), # Indirect Income
        ('G016', 'Reserves & Surplus', 'Liabilities', 'MG005'), # Equity
    ]
    
    # We need to include company_id in the insert
    # Prepare data with company_id
    # Use ON CONFLICT to update master_group_code if it's missing (for existing groups)
    for code, name, nature, mg_code in default_groups:
        cursor.execute("""
            INSERT INTO groups (company_id, group_code, group_name, nature, master_group_code) 
            VALUES (%s, %s, %s, %s, %s) 
            ON CONFLICT (company_id, group_code) 
            DO UPDATE SET master_group_code = EXCLUDED.master_group_code
        """, (company_id, code, name, nature, mg_code))
    
    conn.commit()
    conn.close()

def ensure_default_ledgers(company_id=None):
    """Ensure default ledgers (VAT, Discount) exist for the company"""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return

    conn = get_connection()
    cursor = conn.cursor()

    # Default VAT ledgers
    default_vat_ledgers = [
        ('LVATIN', 'Input VAT 5%', 'G011', 0, 'Debit'),
        ('LVATOUT', 'Output VAT 5%', 'G011', 0, 'Credit'),
    ]
    for code, name, grp, opbal, type_ in default_vat_ledgers:
        cursor.execute("""
            INSERT INTO ledgers
            (company_id, ledger_code, ledger_name, group_code, opening_balance, opening_balance_type, closing_balance)
            VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
        """, (company_id, code, name, grp, opbal, type_, 0))

    # Default Discount ledgers
    default_discount_ledgers = [
        ('LDISCALL', 'Discount Allowed', 'G012', 0, 'Debit'),      # Direct Expenses
        ('LDISCREC', 'Discount Received', 'G014', 0, 'Credit'),    # Direct Income
    ]
    for code, name, grp, opbal, type_ in default_discount_ledgers:
        cursor.execute("""
            INSERT INTO ledgers
            (company_id, ledger_code, ledger_name, group_code, opening_balance, opening_balance_type, closing_balance)
            VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
        """, (company_id, code, name, grp, opbal, type_, 0))
    
    # Default Reserve & Surplus ledger
    cursor.execute("""
        INSERT INTO ledgers
        (company_id, ledger_code, ledger_name, group_code, opening_balance, opening_balance_type, closing_balance)
        VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
    """, (company_id, 'LRESERVE', 'Reserve & Surplus', 'G016', 0, 'Credit', 0))
    
    conn.commit()
    conn.close()

# ========== Groups ==========
def get_master_groups(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT master_group_code, master_group_name, nature FROM master_groups WHERE company_id = %s ORDER BY master_group_name", (company_id,))
    groups = cursor.fetchall()
    conn.close()
    return [dict(g) for g in groups]

def get_groups(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT g.group_code, g.group_name, g.nature, g.master_group_code, mg.master_group_name
        FROM groups g
        LEFT JOIN master_groups mg ON g.master_group_code = mg.master_group_code AND g.company_id = mg.company_id
        WHERE g.company_id = %s 
        ORDER BY g.group_name
    """, (company_id,))
    groups = cursor.fetchall()
    conn.close()
    return [dict(g) for g in groups]

def generate_next_group_code(cursor, company_id):
    """Generate the next available group code (e.g., G001, G002, ...) for the company"""
    cursor.execute("SELECT group_code FROM groups WHERE company_id = %s AND group_code LIKE 'G%%'", (company_id,))
    codes = [row['group_code'] if hasattr(row, 'keys') else row[0] for row in cursor.fetchall()]
    
    max_num = 0
    for code in codes:
        try:
            num = int(code[1:])
            if num > max_num:
                max_num = num
        except ValueError:
            pass
            
    next_num = max_num + 1
    return f"G{next_num:03d}"

def add_group(group_code, group_name, nature, master_group_code=None, db_connection=None, company_id=None):
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
        if not group_code:
            group_code = generate_next_group_code(cursor, company_id)

        cursor.execute(
            "INSERT INTO groups (company_id, group_code, group_name, nature, master_group_code) VALUES (%s, %s, %s, %s, %s)",
            (company_id, group_code, group_name, nature, master_group_code)
        )
        if should_close:
            conn.commit()
        print(f"add_group: {group_code}, {group_name}, {nature}, {master_group_code} (Company: {company_id})")
    except Exception as e:
        raise Exception(f"Error adding group: {str(e)}")
    finally:
        if should_close:
            conn.close()

def delete_group(group_name, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Check ledgers
        cursor.execute(
            "SELECT COUNT(*) FROM ledgers WHERE company_id = %s AND group_code = (SELECT group_code FROM groups WHERE company_id = %s AND group_name = %s)",
            (company_id, company_id, group_name)
        )
        if cursor.fetchone()[0] > 0:
            raise Exception("Cannot delete group: Ledgers exist under this group.")
            
        # Check sub groups
        cursor.execute(
            "SELECT COUNT(*) FROM sub_groups WHERE company_id = %s AND group_code = (SELECT group_code FROM groups WHERE company_id = %s AND group_name = %s)",
            (company_id, company_id, group_name)
        )
        if cursor.fetchone()[0] > 0:
            raise Exception("Cannot delete group: Sub Groups exist under this group.")
            
        cursor.execute("DELETE FROM groups WHERE company_id = %s AND group_name = %s", (company_id, group_name))
        if cursor.rowcount == 0:
            raise Exception("Group not found.")
        conn.commit()
        print(f"delete_group: {group_name} (Company: {company_id})")
    except Exception as e:
        raise Exception(f"Error deleting group: {str(e)}")
    finally:
        conn.close()

# ========== Sub Groups (NEW) ==========

def get_sub_groups(group_code=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    try:
        if group_code:
            cursor.execute("""
                SELECT sg.id, sg.sub_group_name, g.group_name, sg.group_code 
                FROM sub_groups sg 
                JOIN groups g ON sg.group_code = g.group_code AND sg.company_id = g.company_id
                WHERE sg.company_id = %s AND sg.group_code = %s 
                ORDER BY sg.sub_group_name
            """, (company_id, group_code))
        else:
            cursor.execute("""
                SELECT sg.id, sg.sub_group_name, g.group_name, sg.group_code 
                FROM sub_groups sg 
                JOIN groups g ON sg.group_code = g.group_code AND sg.company_id = g.company_id
                WHERE sg.company_id = %s
                ORDER BY g.group_name, sg.sub_group_name
            """, (company_id,))
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        print(f"Error getting sub groups: {e}")
        return []
    finally:
        conn.close()

def add_sub_group(sub_group_name, group_code, db_connection=None, company_id=None):
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
        sub_group_name = sub_group_name.strip()
        cursor.execute("SELECT 1 FROM sub_groups WHERE company_id = %s AND sub_group_name = %s", (company_id, sub_group_name))
        if cursor.fetchone():
            raise Exception("Sub Group Name already exists.")
            
        cursor.execute(
            "INSERT INTO sub_groups (company_id, sub_group_name, group_code) VALUES (%s, %s, %s)",
            (company_id, sub_group_name, group_code)
        )
        if should_close:
            conn.commit()
    except Exception as e:
        raise Exception(f"Error adding sub group: {str(e)}")
    finally:
        if should_close:
            conn.close()

def delete_sub_group(sub_group_name, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Check if used in ledgers
        cursor.execute(
            "SELECT COUNT(*) FROM ledgers WHERE company_id = %s AND sub_group_id = (SELECT id FROM sub_groups WHERE company_id = %s AND sub_group_name = %s)",
            (company_id, company_id, sub_group_name)
        )
        if cursor.fetchone()[0] > 0:
            raise Exception("Cannot delete Sub Group: Ledgers are assigned to it.")
            
        cursor.execute("DELETE FROM sub_groups WHERE company_id = %s AND sub_group_name = %s", (company_id, sub_group_name))
        if cursor.rowcount == 0:
            raise Exception("Sub Group not found.")
        conn.commit()
    except Exception as e:
        raise Exception(f"Error deleting sub group: {str(e)}")
    finally:
        conn.close()

# ========== Ledgers ==========
def get_ledger_details(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT l.ledger_code, l.ledger_name, g.group_code, g.group_name, g.nature,
               l.opening_balance, l.opening_balance_type, l.opening_balance_date, l.closing_balance,
               sg.sub_group_name, l.credit_days, COALESCE(l.is_active, 1) AS is_active
        FROM ledgers l
        JOIN groups g ON l.group_code = g.group_code AND l.company_id = g.company_id
        LEFT JOIN sub_groups sg ON l.sub_group_id = sg.id
        WHERE l.company_id = %s
        ORDER BY l.ledger_name
    """, (company_id,))
    ledgers = cursor.fetchall()
    conn.close()
    return [dict(l) for l in ledgers]

def get_ledgers(group_code=None, exclude_sales_purchase=False, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    try:
        active = "COALESCE(is_active, 1) = 1"
        if group_code:
            if isinstance(group_code, (list, tuple)):
                placeholders = ','.join(['%s'] * len(group_code))
                query = f"SELECT ledger_code, ledger_name, group_code, closing_balance FROM ledgers WHERE company_id = %s AND {active} AND group_code IN ({placeholders})"
                params = [company_id] + list(group_code)
                cursor.execute(query, tuple(params))
            else:
                cursor.execute(
                    f"SELECT ledger_code, ledger_name, group_code, closing_balance FROM ledgers WHERE company_id = %s AND {active} AND group_code = %s",
                    (company_id, group_code)
                )
        elif exclude_sales_purchase:
            cursor.execute(
                f"SELECT ledger_code, ledger_name, group_code, closing_balance FROM ledgers WHERE company_id = %s AND {active} AND group_code NOT IN ('G001', 'G002')",
                (company_id,)
            )
        else:
            cursor.execute(f"SELECT ledger_code, ledger_name, group_code, closing_balance, credit_days FROM ledgers WHERE company_id = %s AND {active}", (company_id,))
        ledgers = cursor.fetchall()
        return [dict(l) for l in ledgers]
    except Exception as e:
        print(f"Error in get_ledgers: {str(e)}")
        return []
    finally:
        conn.close()

def _resolve_opening_location(cursor, company_id, location_name=None):
    """Resolve the location an opening balance should be saved against.
    Falls back to the company's default location, then 'Main Location'."""
    location_name = (location_name or '').strip()
    if location_name:
        return location_name
    cursor.execute(
        "SELECT location_name FROM locations WHERE company_id = %s AND is_active = 1 ORDER BY is_default DESC, location_name LIMIT 1",
        (company_id,)
    )
    row = cursor.fetchone()
    if row:
        return row['location_name'] if hasattr(row, 'keys') else row[0]
    return 'Main Location'

def get_ledger_opening_balances(ledger_code=None, company_id=None):
    """Per-location opening balances. If ledger_code is None, returns all rows for the company."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if ledger_code:
            cursor.execute("""
                SELECT ledger_code, location_name, opening_balance, opening_balance_type, opening_balance_date
                FROM ledger_opening_balances WHERE company_id = %s AND ledger_code = %s
                ORDER BY location_name
            """, (company_id, ledger_code))
        else:
            cursor.execute("""
                SELECT ledger_code, location_name, opening_balance, opening_balance_type, opening_balance_date
                FROM ledger_opening_balances WHERE company_id = %s
                ORDER BY ledger_code, location_name
            """, (company_id,))
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

def upsert_ledger_opening_balance(cursor, company_id, ledger_code, location_name, opening_balance, opening_balance_type, opening_balance_date):
    """Overwrite the opening balance for (ledger, location) — or add it for a new
    location — then recompute the ledger's aggregate opening/closing balances.
    Runs on the caller's cursor/transaction."""
    opening_balance = round(float(opening_balance), 2)
    signed_new = opening_balance if opening_balance_type == 'Debit' else -opening_balance

    # Current signed total of per-location openings for this ledger
    cursor.execute("""
        SELECT COALESCE(SUM(CASE WHEN opening_balance_type = 'Credit' THEN -opening_balance ELSE opening_balance END), 0)
        FROM ledger_opening_balances WHERE company_id = %s AND ledger_code = %s
    """, (company_id, ledger_code))
    old_total = float(cursor.fetchone()[0] or 0)

    cursor.execute("""
        INSERT INTO ledger_opening_balances
            (company_id, ledger_code, location_name, opening_balance, opening_balance_type, opening_balance_date)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (company_id, ledger_code, location_name)
        DO UPDATE SET opening_balance = EXCLUDED.opening_balance,
                      opening_balance_type = EXCLUDED.opening_balance_type,
                      opening_balance_date = EXCLUDED.opening_balance_date
    """, (company_id, ledger_code, location_name, opening_balance, opening_balance_type, opening_balance_date))

    cursor.execute("""
        SELECT COALESCE(SUM(CASE WHEN opening_balance_type = 'Credit' THEN -opening_balance ELSE opening_balance END), 0),
               MIN(opening_balance_date)
        FROM ledger_opening_balances WHERE company_id = %s AND ledger_code = %s
    """, (company_id, ledger_code))
    row = cursor.fetchone()
    new_total = float(row[0] or 0)
    earliest_date = row[1]

    agg_balance = round(abs(new_total), 2)
    agg_type = 'Debit' if new_total >= 0 else 'Credit'
    delta = round(new_total - old_total, 2)
    cursor.execute("""
        UPDATE ledgers
        SET opening_balance = %s, opening_balance_type = %s,
            opening_balance_date = COALESCE(%s, opening_balance_date),
            closing_balance = closing_balance + %s
        WHERE company_id = %s AND ledger_code = %s
    """, (agg_balance, agg_type, earliest_date, delta, company_id, ledger_code))

def add_ledger(ledger_code, ledger_name, group_code, opening_balance, opening_balance_type, sub_group_id=None, credit_days=0, db_connection=None, company_id=None, opening_balance_date=None, location_name=None):
    """Create a ledger, or — if it already exists — upsert its opening balance for
    the given location (overwrite same location, add for a new location). The
    ledger master itself is never duplicated."""
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
        # Normalize inputs
        ledger_code = str(ledger_code).strip()
        ledger_name = str(ledger_name).strip()
        opening_balance_type = str(opening_balance_type).strip()
        opening_balance = round(float(opening_balance), 2)
        closing_balance = opening_balance if opening_balance_type == 'Debit' else -opening_balance

        opening_balance_date = str(opening_balance_date or '').strip()
        if not opening_balance_date:
            raise Exception("Opening Balance Date is required.")
        from datetime import datetime as _dt
        try:
            _dt.strptime(opening_balance_date, '%Y-%m-%d')
        except ValueError:
            raise Exception(f"Invalid Opening Balance Date '{opening_balance_date}'. Expected YYYY-MM-DD.")

        if sub_group_id == '': sub_group_id = None

        target_location = _resolve_opening_location(cursor, company_id, location_name)

        # Existing ledger (by code or name) -> update opening balance for the
        # location instead of raising a duplicate error.
        cursor.execute(
            "SELECT ledger_code, ledger_name FROM ledgers WHERE company_id = %s AND (ledger_code = %s OR ledger_name = %s)",
            (company_id, ledger_code, ledger_name)
        )
        existing = cursor.fetchone()
        if existing:
            existing_code = existing['ledger_code'] if hasattr(existing, 'keys') else existing[0]
            upsert_ledger_opening_balance(
                cursor, company_id, existing_code, target_location,
                opening_balance, opening_balance_type, opening_balance_date
            )
            if should_close:
                conn.commit()
            print(f"add_ledger: existing '{existing_code}' — opening balance set for location '{target_location}' (Company: {company_id})")
            return

        cursor.execute(
            """
            INSERT INTO ledgers
            (company_id, ledger_code, ledger_name, group_code, opening_balance, opening_balance_type, closing_balance, sub_group_id, credit_days, opening_balance_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (company_id, ledger_code, ledger_name, group_code, opening_balance, opening_balance_type, closing_balance, sub_group_id, credit_days, opening_balance_date)
        )
        if opening_balance != 0:
            cursor.execute("""
                INSERT INTO ledger_opening_balances
                    (company_id, ledger_code, location_name, opening_balance, opening_balance_type, opening_balance_date)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (company_id, ledger_code, location_name)
                DO UPDATE SET opening_balance = EXCLUDED.opening_balance,
                              opening_balance_type = EXCLUDED.opening_balance_type,
                              opening_balance_date = EXCLUDED.opening_balance_date
            """, (company_id, ledger_code, target_location, opening_balance, opening_balance_type, opening_balance_date))
        if should_close:
            conn.commit()
        print(f"add_ledger: {ledger_code}, {ledger_name}, location: {target_location} (Company: {company_id})")
    except Exception as e:
        raise Exception(f"Error adding ledger: {str(e)}")
    finally:
        if should_close:
            conn.close()

def delete_ledger(ledger_name, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Check if used in vouchers (TODO: Implement voucher check)
        # For now, just delete
        cursor.execute(
            "DELETE FROM ledger_opening_balances WHERE company_id = %s AND ledger_code = (SELECT ledger_code FROM ledgers WHERE company_id = %s AND ledger_name = %s)",
            (company_id, company_id, ledger_name)
        )
        cursor.execute("DELETE FROM ledgers WHERE company_id = %s AND ledger_name = %s", (company_id, ledger_name))
        if cursor.rowcount == 0:
            raise Exception("Ledger not found.")
        conn.commit()
        print(f"delete_ledger: {ledger_name} (Company: {company_id})")
    except Exception as e:
        raise Exception(f"Error deleting ledger: {str(e)}")
    finally:
        conn.close()

def update_party_details(ledger_code, address=None, contact_person=None, phone=None, email=None, trn=None, company_id=None, db_connection=None):
    """Save Customer/Vendor contact details against a ledger."""
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
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE ledgers SET address = %s, contact_person = %s, phone = %s, email = %s, trn = %s
            WHERE company_id = %s AND ledger_code = %s
        """, ((address or '').strip() or None, (contact_person or '').strip() or None,
              (phone or '').strip() or None, (email or '').strip() or None,
              (trn or '').strip() or None, company_id, ledger_code))
        if should_close:
            conn.commit()
    finally:
        if should_close:
            conn.close()

def get_party_details(ledger_name=None, ledger_code=None, company_id=None):
    """Contact details for a party ledger (by name or code)."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return None
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if ledger_code:
            cursor.execute("SELECT ledger_code, ledger_name, address, contact_person, phone, email, trn FROM ledgers WHERE company_id = %s AND ledger_code = %s", (company_id, ledger_code))
        else:
            cursor.execute("SELECT ledger_code, ledger_name, address, contact_person, phone, email, trn FROM ledgers WHERE company_id = %s AND ledger_name = %s", (company_id, ledger_name))
        r = cursor.fetchone()
        if not r:
            return None
        return {'ledger_code': r[0], 'ledger_name': r[1], 'address': r[2],
                'contact_person': r[3], 'phone': r[4], 'email': r[5], 'trn': r[6]}
    finally:
        conn.close()

def get_party_ledgers(group_code, company_id=None):
    """Customers (G007) or Vendors (G008) with their contact details."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ledger_code, ledger_name, opening_balance, opening_balance_type, closing_balance,
                   credit_days, address, contact_person, phone, email, trn, COALESCE(is_active, 1) AS is_active
            FROM ledgers WHERE company_id = %s AND group_code = %s
            ORDER BY ledger_name
        """, (company_id, group_code))
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

def set_ledger_active(ledger_code, is_active, company_id=None):
    """Block (0) or unblock (1) a ledger. Blocked ledgers stay in reports and
    history but are hidden from new-voucher ledger lists."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE ledgers SET is_active = %s WHERE company_id = %s AND ledger_code = %s",
            (1 if is_active else 0, company_id, ledger_code)
        )
        if cursor.rowcount == 0:
            raise Exception("Ledger not found.")
        conn.commit()
    finally:
        conn.close()

def update_ledger_master(ledger_code, ledger_name, group_code, sub_group_id=None, company_id=None):
    """Edit a ledger's name and group. Renames cascade to all transaction
    tables that reference the ledger by name."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

    ledger_name = str(ledger_name).strip()
    if not ledger_name:
        raise Exception("Ledger name is required")
    if sub_group_id == '':
        sub_group_id = None

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT ledger_name FROM ledgers WHERE company_id = %s AND ledger_code = %s", (company_id, ledger_code))
        row = cursor.fetchone()
        if not row:
            raise Exception("Ledger not found.")
        old_name = row[0]

        # New name must not belong to another ledger
        cursor.execute(
            "SELECT ledger_code FROM ledgers WHERE company_id = %s AND LOWER(TRIM(ledger_name)) = LOWER(%s) AND ledger_code != %s",
            (company_id, ledger_name, ledger_code)
        )
        clash = cursor.fetchone()
        if clash:
            raise Exception(f"Ledger name '{ledger_name}' is already used by ledger {clash[0]}.")

        if old_name != ledger_name:
            # FKs on ledger_entries/item_entries/settlements are ON UPDATE
            # CASCADE (see unified_db migration), so renaming the ledger row
            # cascades to all transaction tables automatically.
            cursor.execute("UPDATE ledgers SET ledger_name = %s WHERE company_id = %s AND ledger_code = %s", (ledger_name, company_id, ledger_code))

        cursor.execute(
            "UPDATE ledgers SET group_code = %s, sub_group_id = %s WHERE company_id = %s AND ledger_code = %s",
            (group_code, sub_group_id, company_id, ledger_code)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def update_ledger_credit_terms(ledger_code, credit_days, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE ledgers SET credit_days = %s WHERE company_id = %s AND ledger_code = %s", (credit_days, company_id, ledger_code))
        conn.commit()
    except Exception as e:
        raise Exception(f"Error updating credit terms: {str(e)}")
    finally:
        conn.close()

# ========== Cost Centers ==========
def get_cost_centers(active_only=False, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    try:
        if active_only:
            cursor.execute("SELECT center_code, center_name, is_active FROM cost_centers WHERE company_id = %s AND is_active = 1", (company_id,))
        else:
            cursor.execute("SELECT center_code, center_name, is_active FROM cost_centers WHERE company_id = %s", (company_id,))
        centers = cursor.fetchall()
        return [dict(c) for c in centers]
    except Exception as e:
        print(f"Error in get_cost_centers: {str(e)}")
        return []
    finally:
        conn.close()

def add_cost_center(center_code, center_name, db_connection=None, company_id=None):
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
        cursor.execute(
            "INSERT INTO cost_centers (company_id, center_code, center_name, is_active) VALUES (%s, %s, %s, 1)",
            (company_id, center_code, center_name)
        )
        if should_close:
            conn.commit()
        print(f"add_cost_center: {center_code}, {center_name} (Company: {company_id})")
    except Exception as e:
        raise Exception(f"Error adding cost center: {str(e)}")
    finally:
        if should_close:
            conn.close()

def update_cost_center_status(center_name, is_active, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE cost_centers SET is_active = %s WHERE company_id = %s AND center_name = %s",
            (1 if is_active else 0, company_id, center_name)
        )
        conn.commit()
        print(f"update_cost_center_status: {center_name} -> {is_active} (Company: {company_id})")
    except Exception as e:
        raise Exception(f"Error updating cost center status: {str(e)}")
    finally:
        conn.close()

def delete_cost_center(center_name, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM cost_centers WHERE company_id = %s AND center_name = %s", (company_id, center_name))
        if cursor.rowcount == 0:
            raise Exception("Cost center not found.")
        conn.commit()
        print(f"delete_cost_center: {center_name} (Company: {company_id})")
    except Exception as e:
        raise Exception(f"Error deleting cost center: {str(e)}")
    finally:
        conn.close()

