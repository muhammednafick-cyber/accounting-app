# import sqlite3 - removed
from datetime import datetime
from .config import get_connection, execute_insert_returning_id
from .company_db import get_current_company_id

def _init_recurring_tables():
    """Initialize recurring voucher tables"""
    # Logic moved to unified_db.py
    pass

# Every voucher this posts is a double entry, so a template that does not
# balance cannot produce one. The check lives here rather than in the route
# because both the screen and the scheduled processing must apply it - the
# processing path calls add_voucher directly, which only logs an imbalance.
BALANCE_TOLERANCE = 0.01


def validate_ledger_entries(entries):
    """Total debit for a balanced set of entries. Raises ValueError otherwise."""
    if not entries:
        raise ValueError("Add at least one ledger entry.")

    total_debit = total_credit = 0.0
    for row, entry in enumerate(entries, start=1):
        name = (entry.get("ledger_name") or "").strip()
        if not name:
            raise ValueError(f"Row {row}: choose a ledger.")
        try:
            amount = float(entry.get("amount") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"Row {row}: '{entry.get('amount')}' is not an amount.")
        if amount <= 0:
            raise ValueError(f"Row {row}: the amount must be more than zero.")

        side = entry.get("type")
        if side == "Debit":
            total_debit += amount
        elif side == "Credit":
            total_credit += amount
        else:
            raise ValueError(f"Row {row}: choose Debit or Credit.")

    if not total_debit or not total_credit:
        raise ValueError(
            "A voucher needs both sides: enter at least one debit and one credit.")

    if abs(total_debit - total_credit) > BALANCE_TOLERANCE:
        raise ValueError(
            f"Debit {total_debit:,.2f} and Credit {total_credit:,.2f} do not match "
            f"- a difference of {abs(total_debit - total_credit):,.2f}.")

    return round(total_debit, 2)


def get_recurring_templates(company_id=None):
    """Every template for this company, newest due first."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT * FROM recurring_templates WHERE company_id = %s "
            "ORDER BY active DESC, next_due_date, template_name", (company_id,))
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_recurring_template(template_id, company_id=None):
    """One template, or None. Always scoped to the company."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return None

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT * FROM recurring_templates WHERE id = %s AND company_id = %s",
            (template_id, company_id))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_recurring_template(template_id, template_name, voucher_type, frequency,
                              next_due_date, ledger_details_json, amount=0,
                              narration="", active=1, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required to update a recurring template")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE recurring_templates
               SET template_name = %s, voucher_type = %s, frequency = %s,
                   next_due_date = %s, ledger_details_json = %s, amount = %s,
                   narration = %s, active = %s
             WHERE id = %s AND company_id = %s
            """,
            (template_name, voucher_type, frequency, next_due_date,
             ledger_details_json, amount, narration, active,
             template_id, company_id))
        if not cursor.rowcount:
            raise ValueError("That template no longer exists.")
        conn.commit()
        return template_id
    finally:
        conn.close()


def delete_recurring_template(template_id, company_id=None):
    """Remove a template. The vouchers it already posted are not touched."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required to delete a recurring template")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM recurring_templates WHERE id = %s AND company_id = %s",
            (template_id, company_id))
        deleted = cursor.rowcount
        conn.commit()
        return bool(deleted)
    finally:
        conn.close()


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

    # add_voucher only logs an imbalance, so an older template saved before the
    # form checked would post a one-sided voucher and quietly break the books.
    # Refuse it here and name the template, so it can be corrected.
    try:
        validate_ledger_entries(ledgers)
    except ValueError as exc:
        conn.close()
        raise ValueError(
            f"'{template['template_name']}' does not balance, so it cannot be "
            f"posted: {exc} Edit the template and try again.")

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
