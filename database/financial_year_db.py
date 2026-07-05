# import sqlite3 - removed
from datetime import datetime
from .config import get_connection, execute_insert_returning_id
from .company_db import get_current_company_id

def _initialize_financial_years_table():
    """Initialize financial_years table"""
    # Logic in unified_db.py
    pass

def create_fy(fy_code, start_date, end_date, company_id=None):
    """
    Create a new Financial Year.
    start_date and end_date should be YYYY-MM-DD.
    """
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required to create a Financial Year")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        if start_date >= end_date:
            raise Exception("Start Date must be earlier than End Date")
            
        # Basic validation: Check overlap
        cursor.execute("""
            SELECT fy_code FROM financial_years 
            WHERE company_id = %s AND (
                (start_date <= %s AND end_date >= %s) OR (start_date <= %s AND end_date >= %s)
            )
        """, (company_id, end_date, start_date, end_date, start_date))
        overlap = cursor.fetchone()
        if overlap:
            raise Exception(f"New FY overlaps with existing FY: {overlap[0]}")

        fy_id = execute_insert_returning_id(
            cursor,
            "INSERT INTO financial_years (company_id, fy_code, start_date, end_date) VALUES (%s, %s, %s, %s)",
            (company_id, fy_code, start_date, end_date)
        )
        conn.commit()
        print(f"Created FY: {fy_code} for Company {company_id}")
        return fy_id
    except Exception as e:
        raise Exception(f"Error creating FY: {str(e)}")
    finally:
        conn.close()

def update_fy(fy_id, start_date, end_date, company_id=None):
    """
    Update Financial Year dates with strict validation logic.
    """
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required to update a Financial Year")

    if start_date >= end_date:
        raise Exception("Start Date must be earlier than End Date")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Get Current FY Details
        cursor.execute("SELECT start_date, end_date FROM financial_years WHERE id = %s AND company_id = %s", (fy_id, company_id))
        current_fy = cursor.fetchone()
        if not current_fy:
             raise Exception("Financial Year not found")
        
        old_start, old_end = current_fy
        
        # Check for overlaps with OTHER FYs
        cursor.execute("""
            SELECT fy_code FROM financial_years 
            WHERE company_id = %s AND id != %s AND (
                (start_date <= %s AND end_date >= %s) OR (start_date <= %s AND end_date >= %s)
            )
        """, (company_id, fy_id, end_date, start_date, end_date, start_date))
        overlap = cursor.fetchone()
        if overlap:
            raise Exception(f"Modified FY overlaps with existing FY: {overlap[0]}")

        # Check for Vouchers in this Period
        # We check vouchers that fall within the OLD range
        cursor.execute("SELECT MIN(date), MAX(date) FROM vouchers WHERE company_id = %s AND date >= %s AND date <= %s", (company_id, old_start, old_end))
        min_v_date, max_v_date = cursor.fetchone()
        
        if min_v_date and max_v_date:
            # Vouchers Exist!
            print(f"FY has vouchers from {min_v_date} to {max_v_date}")
            
            # Rule 1: End Date cannot be lower than Last Posted Entry
            if end_date < max_v_date:
                raise Exception(f"Cannot set End Date to {end_date}. Last posted entry is on {max_v_date}.")
            
            # Rule 2: Start Date Logic? User said "IF NO ENTRY... IT CAN EDIT BOTH". 
            # Implication: If entry exists, Start Date is locked? 
            # Or Start Date cannot be HIGHER than First Posted Entry?
            # Let's enforce: Start Date must be <= min_v_date
            if start_date > min_v_date:
                 raise Exception(f"Cannot set Start Date to {start_date}. First posted entry is on {min_v_date}.")
            
            # If dates span the existing vouchers, it is permitted to Expand/Contract(safely).
        else:
            # No vouchers -> Free to edit both
            pass

        cursor.execute(
            "UPDATE financial_years SET start_date = %s, end_date = %s WHERE id = %s AND company_id = %s",
            (start_date, end_date, fy_id, company_id)
        )
        conn.commit()
        print(f"Updated FY ID: {fy_id}")
    except Exception as e:
        raise Exception(f"Error updating FY: {str(e)}")
    finally:
        conn.close()

def get_all_fys(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    # conn.row_factory = sqlite3.Row - Removed
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM financial_years WHERE company_id = %s ORDER BY start_date DESC", (company_id,))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

def get_fy_by_date(date_str, company_id=None):
    """
    Get the Financial Year object for a specific date (YYYY-MM-DD).
    Returns dict or None.
    """
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return None

    conn = get_connection()
    # conn.row_factory = sqlite3.Row - Removed
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT * FROM financial_years WHERE company_id = %s AND start_date <= %s AND end_date >= %s",
            (company_id, date_str, date_str)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_fy_by_id(fy_id, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    
    conn = get_connection()
    # conn.row_factory = sqlite3.Row - Removed
    cursor = conn.cursor()
    try:
        if company_id:
            cursor.execute("SELECT * FROM financial_years WHERE id = %s AND company_id = %s", (fy_id, company_id))
        else:
            # If no company_id provided, just get by ID (legacy support or admin)
            cursor.execute("SELECT * FROM financial_years WHERE id = %s", (fy_id,))
            
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def lock_fy(fy_id, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required to lock a Financial Year")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE financial_years SET is_locked = 1 WHERE id = %s AND company_id = %s", (fy_id, company_id))
        conn.commit()
        print(f"Locked FY ID: {fy_id}")
    finally:
        conn.close()

def reopen_fy(fy_id, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required to reopen a Financial Year")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE financial_years SET is_locked = 0 WHERE id = %s AND company_id = %s", (fy_id, company_id))
        conn.commit()
        print(f"Re-opened FY ID: {fy_id}")
    finally:
        conn.close()
