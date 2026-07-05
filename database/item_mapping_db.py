import psycopg2
from .config import get_connection
from .company_db import get_current_company_id

def init_item_mapping_table():
    """Initialize the vendor_item_mappings table."""
    # Legacy initialization logic, should be replaced by unified schema creation if possible.
    pass

def get_item_mapping(vendor_name, vendor_item_name, company_id=None):
    """
    Retrieve the app item code for a given vendor and their item name.
    case-insensitive search for vendor_item_name could be better, but strict for now.
    """
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT app_item_code FROM vendor_item_mappings 
        WHERE vendor_name = %s AND vendor_item_name = %s AND company_id = %s
    """, (vendor_name, vendor_item_name, company_id))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def add_item_mapping(vendor_name, vendor_item_name, app_item_code, company_id=None):
    """
    Add or update an item mapping.
    """
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO vendor_item_mappings (company_id, vendor_name, vendor_item_name, app_item_code)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT(company_id, vendor_name, vendor_item_name) 
            DO UPDATE SET app_item_code = excluded.app_item_code
        """, (company_id, vendor_name, vendor_item_name, app_item_code))
        conn.commit()
        print(f"Mapped {vendor_name}: {vendor_item_name} -> {app_item_code} (Company: {company_id})")
        return True
    except Exception as e:
        print(f"Error adding mapping: {e}")
        return False
    finally:
        conn.close()

def get_all_mappings(company_id=None):
    """
    Return all mappings (limit 500 for UI).
    """
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT vendor_name, vendor_item_name, app_item_code FROM vendor_item_mappings WHERE company_id = %s LIMIT 500", (company_id,))
    rows = cursor.fetchall()
    conn.close()
    return [{"vendor": r[0], "vendor_item": r[1], "app_item": r[2]} for r in rows]

def delete_item_mapping(vendor_name, vendor_item_name, company_id=None):
    """
    Delete a specific mapping.
    """
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            DELETE FROM vendor_item_mappings 
            WHERE vendor_name = %s AND vendor_item_name = %s AND company_id = %s
        """, (vendor_name, vendor_item_name, company_id))
        conn.commit()
        deleted = cursor.rowcount > 0
        return deleted
    except Exception as e:
        print(f"Error deleting mapping: {e}")
        return False
    finally:
        conn.close()

def get_mappings_by_vendor(vendor_name, company_id=None):
    """
    Return all mappings for a specific vendor.
    """
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT vendor_name, vendor_item_name, app_item_code 
        FROM vendor_item_mappings 
        WHERE vendor_name = %s AND company_id = %s
    """, (vendor_name, company_id))
    rows = cursor.fetchall()
    conn.close()
    return [{"vendor": r[0], "vendor_item": r[1], "app_item": r[2]} for r in rows]
