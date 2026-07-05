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

def get_next_settlement_number(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Use MAX logic to avoid collisions if settlements are deleted
        cursor.execute("SELECT settlement_number FROM settlements WHERE settlement_number LIKE 'S-%%' AND company_id = %s ORDER BY length(settlement_number) DESC, settlement_number DESC LIMIT 1", (company_id,))
        row = cursor.fetchone()
        if row:
            last_no = row['settlement_number'] if isinstance(row, dict) or hasattr(row, 'keys') else row[0]
            try:
                parts = last_no.split('-')
                last_seq = int(parts[-1])
                new_seq = last_seq + 1
            except (ValueError, IndexError):
                new_seq = 1
        else:
            new_seq = 1

        return f"S-{str(new_seq).zfill(5)}"
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
            s_num = get_next_settlement_number(company_id=company_id)
        else:
            cursor.execute("SELECT settlement_number FROM settlements WHERE settlement_number LIKE 'S-%%' AND company_id = %s ORDER BY length(settlement_number) DESC, settlement_number DESC LIMIT 1", (company_id,))
            row = cursor.fetchone()
            if row:
                last_no = row['settlement_number'] if isinstance(row, dict) or hasattr(row, 'keys') else row[0]
                try:
                    parts = last_no.split('-')
                    last_seq = int(parts[-1])
                    new_seq = last_seq + 1
                except (ValueError, IndexError):
                    new_seq = 1
            else:
                new_seq = 1
            s_num = f"S-{str(new_seq).zfill(5)}"
        
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
