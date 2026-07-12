"""
Settlements module: Matching/Unmatching handling
"""
from .config import get_connection, execute_insert_returning_id
from .company_db import get_current_company_id
from datetime import datetime

def init_settlement_tables():
    """Initialize settlement-related tables"""
    # This function is likely legacy if we are moving to a unified schema creation script,
    # but we keep it updated for now or for localized initialization.
    pass 

def _settlement_fy_prefix(date_str, company_id):
    """FY-tagged settlement prefix, e.g. FY23-S. Falls back to plain S when no
    FY covers the date."""
    from .financial_year_db import get_fy_by_date
    try:
        fy = get_fy_by_date(date_str, company_id=company_id) if date_str else None
        if fy:
            return f"FY{str(fy['start_date'])[2:4]}-S"
    except Exception:
        pass
    return "S"

def _next_settlement_seq(cursor, company_id, full_prefix):
    cursor.execute(
        "SELECT settlement_number FROM settlements WHERE settlement_number LIKE %s AND company_id = %s ORDER BY length(settlement_number) DESC, settlement_number DESC LIMIT 1",
        (f"{full_prefix}-%", company_id)
    )
    row = cursor.fetchone()
    if row:
        last_no = row['settlement_number'] if isinstance(row, dict) or hasattr(row, 'keys') else row[0]
        try:
            return int(last_no.split('-')[-1]) + 1
        except (ValueError, IndexError):
            return 1
    return 1

def get_next_settlement_number(company_id=None, date_str=None):
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    try:
        from datetime import datetime as _dt
        full_prefix = _settlement_fy_prefix(date_str or _dt.today().strftime('%Y-%m-%d'), company_id)
        new_seq = _next_settlement_seq(cursor, company_id, full_prefix)

        from .vouchers_db import apply_voucher_number_settings
        new_seq = apply_voucher_number_settings(cursor, company_id, 'Settlement', full_prefix, new_seq)

        return f"{full_prefix}-{str(new_seq).zfill(6)}"
    finally:
        conn.close()

def create_settlement(ledger_name, settlement_date, allocations, auto_posted_voucher=None, description="", company_id=None, db_connection=None):
    """
    allocations: list of dict { 'ledger_entry_id': int, 'assigned_amount': float, 'type': 'Debit'|'Credit' }
    """
    if company_id is None:
        company_id = get_current_company_id()

    if db_connection:
        conn = db_connection
        should_close = False
    else:
        conn = get_connection()
        should_close = True
    cursor = conn.cursor()
    try:
        if should_close:
            s_num = get_next_settlement_number(company_id=company_id, date_str=settlement_date)
        else:
            full_prefix = _settlement_fy_prefix(settlement_date, company_id)
            new_seq = _next_settlement_seq(cursor, company_id, full_prefix)
            from .vouchers_db import apply_voucher_number_settings
            new_seq = apply_voucher_number_settings(cursor, company_id, 'Settlement', full_prefix, new_seq)
            s_num = f"{full_prefix}-{str(new_seq).zfill(6)}"
        
        # Calculate total settled amount (sum of debits or sum of credits - they should conceptually match after auto-post)
        total_amt = sum(float(a['assigned_amount']) for a in allocations) / 2.0 
        
        # Use execute_insert_returning_id for PostgreSQL compatibility
        settlement_id = execute_insert_returning_id(
            cursor,
            """
            INSERT INTO settlements (company_id, settlement_number, settlement_date, ledger_name, total_amount, description, auto_posted_voucher_number)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (company_id, s_num, settlement_date, ledger_name, total_amt, description, auto_posted_voucher)
        )
        
        for alloc in allocations:
            cursor.execute("""
                INSERT INTO settlement_allocations (company_id, settlement_id, ledger_entry_id, assigned_amount, type)
                VALUES (%s, %s, %s, %s, %s)
            """, (company_id, settlement_id, alloc['ledger_entry_id'], alloc['assigned_amount'], alloc['type']))
            
        if should_close:
            conn.commit()
        return s_num
        
    except Exception as e:
        if should_close:
            conn.rollback()
        raise e
    finally:
        if should_close:
            conn.close()

def get_settlements_by_ledger(ledger_name, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM settlements WHERE ledger_name = %s AND company_id = %s ORDER BY settlement_date DESC", (ledger_name, company_id))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def delete_settlement(settlement_id, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Get settlement info to check for auto-posted voucher
        cursor.execute("SELECT auto_posted_voucher_number FROM settlements WHERE id = %s AND company_id = %s", (settlement_id, company_id))
        row = cursor.fetchone()
        if not row:
            raise ValueError("Settlement not found")
            
        auto_voucher = row['auto_posted_voucher_number'] if isinstance(row, dict) or hasattr(row, 'keys') else row[0]
        
        # Delete allocations first
        cursor.execute("DELETE FROM settlement_allocations WHERE settlement_id = %s AND company_id = %s", (settlement_id, company_id))
        
        # Delete settlement header
        cursor.execute("DELETE FROM settlements WHERE id = %s AND company_id = %s", (settlement_id, company_id))
        
        if auto_voucher:
             # Check if it exists and delete it
             cursor.execute("DELETE FROM ledger_entries WHERE voucher_number = %s AND company_id = %s", (auto_voucher, company_id))
             cursor.execute("DELETE FROM vouchers WHERE voucher_number = %s AND company_id = %s", (auto_voucher, company_id))
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def get_settlement_details(settlement_id, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Get Allocations with Voucher Details
        cursor.execute("""
            SELECT sa.assigned_amount, sa.type as allocation_type,
                   v.voucher_number, v.voucher_type, v.date, v.narration,
                   le.amount as original_amount, le.type as entry_type
            FROM settlement_allocations sa
            JOIN ledger_entries le ON sa.ledger_entry_id = le.id AND sa.company_id = le.company_id
            JOIN vouchers v ON le.voucher_number = v.voucher_number AND le.company_id = v.company_id
            WHERE sa.settlement_id = %s AND sa.company_id = %s
        """, (settlement_id, company_id))
        
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
