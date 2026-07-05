# import sqlite3 - removed
from datetime import datetime
from .config import get_connection, execute_insert_returning_id
from .company_db import get_current_company_id

def _init_recurring_tables():
    """Initialize recurring voucher tables"""
    # Logic moved to unified_db.py
    pass

def add_recurring_template(template_name, voucher_type, frequency, next_due_date, ledger_details_json, amount=0, narration="", company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required to add a recurring template")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        new_id = execute_insert_returning_id(
            cursor,
            """
            INSERT INTO recurring_templates (company_id, template_name, voucher_type, frequency, next_due_date, ledger_details_json, amount, narration)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (company_id, template_name, voucher_type, frequency, next_due_date, ledger_details_json, amount, narration)
        )
        conn.commit()
        return new_id
    finally:
        conn.close()

def get_due_recurring_entries(target_date, company_id=None):
    """
    Get all templates that are due on or before target_date.
    """
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    # conn.row_factory = sqlite3.Row - Removed
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM recurring_templates 
        WHERE company_id = %s AND active = 1 AND next_due_date <= %s
    """, (company_id, target_date))
    
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def process_recurring_entry(template_id, posting_date, company_id=None):
    """
    Create the voucher for a template and update the next due date.
    """
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required to process recurring entry")

    from .vouchers_db import add_voucher
    import json
    from datetime import timedelta
    
    conn = get_connection()
    # conn.row_factory = sqlite3.Row - Removed
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM recurring_templates WHERE id = %s AND company_id = %s", (template_id, company_id))
    template = cursor.fetchone()
    if not template:
        conn.close()
        raise ValueError("Template not found")
        
    # Parse data
    ledgers = json.loads(template['ledger_details_json'])
    
    # Create Voucher
    voucher_no = add_voucher(
        voucher_type=template['voucher_type'],
        date=posting_date,
        ledger_entries=ledgers,
        item_entries=[], # Recurring usually is for Expenses/Journals (no items) for now
        narration=template['narration'] + f" (Recurring {template['frequency']})",
        company_id=company_id
    )
    
    # Update Next Due Date
    if isinstance(template['next_due_date'], str):
        current_due = datetime.strptime(template['next_due_date'], "%Y-%m-%d")
    else:
        current_due = template['next_due_date'] # Postgres might return datetime
        
    next_due = current_due
    
    if template['frequency'] == 'Monthly':
        # Add month (naive implementation)
        month = current_due.month + 1
        year = current_due.year
        if month > 12:
            month = 1
            year += 1
        # Handle end of month days (e.g. Jan 31 -> Feb 28)
        try:
            next_due = current_due.replace(year=year, month=month)
        except ValueError:
            next_due = current_due + timedelta(days=30)
            
    elif template['frequency'] == 'Weekly':
        next_due = current_due + timedelta(days=7)
    elif template['frequency'] == 'Daily':
        next_due = current_due + timedelta(days=1)
    elif template['frequency'] == 'Yearly':
        try:
            next_due = current_due.replace(year=current_due.year + 1)
        except ValueError:
             next_due = current_due + timedelta(days=365)

    cursor.execute("UPDATE recurring_templates SET next_due_date = %s WHERE id = %s AND company_id = %s", 
                   (next_due.strftime("%Y-%m-%d"), template_id, company_id))
    
    conn.commit()
    conn.close()
    
    return voucher_no
