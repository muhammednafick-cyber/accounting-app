import psycopg2
from .config import get_connection
from .company_db import get_current_company_id

def init_ai_settings_table():
    """Initialize the ai_settings table."""
    # Logic moved to unified_db.init_unified_db
    pass

def get_ai_setting(key, default=None, company_id=None):
    """
    Retrieve a setting value by key.
    Returns default if key not found.
    """
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT setting_value FROM ai_settings WHERE setting_key = %s AND company_id = %s", (key, company_id))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else default

def set_ai_setting(key, value, company_id=None):
    """
    Set or update a setting value.
    """
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO ai_settings (company_id, setting_key, setting_value) 
            VALUES (%s, %s, %s)
            ON CONFLICT(company_id, setting_key) DO UPDATE SET setting_value = excluded.setting_value
        """, (company_id, key, value))
        conn.commit()
        print(f"Set AI setting: {key} = {value} (Company: {company_id})")
        return True
    except Exception as e:
        print(f"Error setting AI config {key}: {e}")
        return False
    finally:
        conn.close()

def get_all_ai_settings(company_id=None):
    """
    Returns a dictionary of all AI settings.
    """
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT setting_key, setting_value FROM ai_settings WHERE company_id = %s", (company_id,))
    rows = cursor.fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}
