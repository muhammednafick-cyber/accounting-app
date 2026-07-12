"""
Company profile and settings module
"""

# import sqlite3 - removed
import os
import json
from .config import get_connection

def get_current_company_id():
    """
    Helper to get the current company_id from Flask session.
    Returns None if not in a request context or not set.
    """
    try:
        from flask import session
        return session.get('company_id')
    except (ImportError, RuntimeError):
        return None

def _initialize_company_table():
    """
    Initialize company settings table.
    Now handled by unified_db.init_unified_db.
    """
    pass

def company_exists(company_id=None):
    """Check if company profile exists"""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return False

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM company_settings WHERE company_id = %s", (company_id,))
    exists = cursor.fetchone()[0] > 0
    conn.close()
    return exists

def create_company_profile(company_name, company_id=None, **kwargs):
    """Create new company profile"""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        print("Error: company_id required for create_company_profile")
        return False

    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO company_settings (
                company_id, company_name, vat_registration_number, address_line1, 
                address_line2, city, state, country, postal_code, 
                phone, email, inventory_applicable, vat_applicable,
                multiple_locations_applicable, cost_center_applicable, cost_center_mandatory,
                financial_year_start, currency_code
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            company_id,
            company_name,
            kwargs.get('vat_registration_number', ''),
            kwargs.get('address_line1', ''),
            kwargs.get('address_line2', ''),
            kwargs.get('city', ''),
            kwargs.get('state', ''),
            kwargs.get('country', ''),
            kwargs.get('postal_code', ''),
            kwargs.get('phone', ''),
            kwargs.get('email', ''),
            kwargs.get('inventory_applicable', 1),
            kwargs.get('vat_applicable', 1),
            kwargs.get('multiple_locations_applicable', 0),
            kwargs.get('cost_center_applicable', 0),
            kwargs.get('cost_center_mandatory', 0),
            kwargs.get('financial_year_start', '01-01'),
            kwargs.get('currency_code', 'AED')
        ))
        conn.commit()
        
        # Initialize default data for the new company
        try:
            from .accounts_db import ensure_default_groups, ensure_default_ledgers
            from .inventory_db import ensure_default_units
            
            print(f"Initializing defaults for company {company_id}...")
            ensure_default_groups(company_id)
            ensure_default_ledgers(company_id)
            ensure_default_units(company_id)
            print(f"Defaults initialized for company {company_id}")
        except Exception as e:
            print(f"Error initializing company defaults: {e}")
            
        return True
    except Exception as e:
        print(f"Error creating company profile: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def get_company_settings(company_id=None):
    """Get company settings as dictionary"""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return None

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM company_settings WHERE company_id = %s", (company_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return None
    
    # row is likely a dict if using DictCursor
    if isinstance(row, dict) or hasattr(row, 'keys'):
        return dict(row)
    
    columns = [
        'company_id', 'company_name', 'vat_registration_number', 'address_line1',
        'address_line2', 'city', 'state', 'country', 'postal_code',
        'phone', 'email', 'inventory_applicable', 'vat_applicable',
        'multiple_locations_applicable', 'cost_center_applicable', 'cost_center_mandatory',
        'financial_year_start', 'currency_code', 'created_at', 'updated_at'
    ]
    
    return dict(zip(columns, row))

def update_company_profile(company_id=None, **kwargs):
    """Update company profile"""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return False

    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            UPDATE company_settings SET
                company_name = %s,
                vat_registration_number = %s,
                address_line1 = %s,
                address_line2 = %s,
                city = %s,
                state = %s,
                country = %s,
                postal_code = %s,
                phone = %s,
                email = %s,
                inventory_applicable = %s,
                vat_applicable = %s,
                multiple_locations_applicable = %s,
                cost_center_applicable = %s,
                cost_center_mandatory = %s,
                financial_year_start = %s,
                currency_code = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE company_id = %s
        """, (
            kwargs.get('company_name'),
            kwargs.get('vat_registration_number', ''),
            kwargs.get('address_line1', ''),
            kwargs.get('address_line2', ''),
            kwargs.get('city', ''),
            kwargs.get('state', ''),
            kwargs.get('country', ''),
            kwargs.get('postal_code', ''),
            kwargs.get('phone', ''),
            kwargs.get('email', ''),
            kwargs.get('inventory_applicable', 1),
            kwargs.get('vat_applicable', 1),
            kwargs.get('multiple_locations_applicable', 0),
            kwargs.get('cost_center_applicable', 0),
            kwargs.get('cost_center_mandatory', 0),
            kwargs.get('financial_year_start', '01-01'),
            kwargs.get('currency_code', 'AED'),
            company_id
        ))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error updating company profile: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

# ===== RECENT COMPANIES (Deprecated or updated to use company_id) =====

def get_recent_companies():
    # This was file-based. In multi-company DB, we might not need this anymore
    # or we can store it in user preferences.
    # For now, return empty to avoid errors.
    return []

def add_recent_company(db_path, company_name):
    pass

# ===== LOCATIONS/GODOWNS MANAGEMENT =====

def _initialize_locations_table():
    """Initialize locations/godowns table"""
    # Handled by Unified DB
    pass

def get_locations(company_id=None):
    """Get all active locations"""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, location_code, location_name, address, contact_person, 
               phone, is_default, is_active 
        FROM locations 
        WHERE company_id = %s AND is_active = 1
        ORDER BY is_default DESC, location_name
    """, (company_id,))
    locations = cursor.fetchall()
    conn.close()
    return [dict(l) for l in locations]

def get_all_locations(company_id=None):
    """Get all locations including inactive"""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, location_code, location_name, address, contact_person, 
               phone, is_default, is_active 
        FROM locations 
        WHERE company_id = %s
        ORDER BY is_default DESC, location_name
    """, (company_id,))
    locations = cursor.fetchall()
    conn.close()
    return [dict(l) for l in locations]

def get_user_locations(user_id, company_id=None):
    """Locations a user is allowed to use. Empty list = no restriction (all)."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id or not user_id:
        return []
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT location_name FROM user_locations WHERE user_id = %s AND company_id = %s ORDER BY location_name",
            (user_id, company_id)
        )
        return [r[0] for r in cursor.fetchall()]
    finally:
        conn.close()

def set_user_locations(user_id, location_names, company_id=None):
    """Replace the set of locations a user may use (empty = unrestricted)."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id or not user_id:
        raise Exception("Company and user are required")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM user_locations WHERE user_id = %s AND company_id = %s", (user_id, company_id))
        for name in location_names or []:
            name = (name or '').strip()
            if name:
                cursor.execute(
                    "INSERT INTO user_locations (user_id, company_id, location_name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (user_id, company_id, name)
                )
        conn.commit()
    finally:
        conn.close()

def coerce_allowed_location(location_name, user_id=None, is_admin=False, company_id=None):
    """Clamp a location to the user's allowed set. Admins and unrestricted users
    pass through unchanged; a restricted user gets their first allowed location
    when the requested one isn't allowed (or none was requested)."""
    if is_admin or not user_id:
        return location_name
    allowed = get_user_locations(user_id, company_id=company_id)
    if not allowed:
        return location_name
    if location_name in allowed:
        return location_name
    return allowed[0]

def _rehome_unassigned_location_data(cursor, company_id, target_location_name):
    """Move data recorded before locations were enabled — under the placeholder
    'Main Location' or with no location at all — onto the real default location.
    Called whenever a default location is created or changed, so legacy opening
    balances and transactions show up in location-filtered reports."""
    target = (target_location_name or '').strip()
    if not target or target == 'Main Location':
        return
    # If a real location literally named 'Main Location' exists, its data is
    # correctly assigned — nothing to re-home.
    cursor.execute(
        "SELECT 1 FROM locations WHERE company_id = %s AND location_name = 'Main Location'",
        (company_id,)
    )
    if cursor.fetchone():
        return

    # Ledger opening balances: merge 'Main Location' rows into the target.
    cursor.execute("""
        SELECT ledger_code, opening_balance, opening_balance_type
        FROM ledger_opening_balances
        WHERE company_id = %s AND location_name = 'Main Location'
    """, (company_id,))
    for code, bal, btype in cursor.fetchall():
        signed = float(bal or 0) if (btype or 'Debit') == 'Debit' else -float(bal or 0)
        cursor.execute("""
            SELECT id, opening_balance, opening_balance_type FROM ledger_opening_balances
            WHERE company_id = %s AND ledger_code = %s AND location_name = %s
        """, (company_id, code, target))
        existing = cursor.fetchone()
        if existing:
            t_signed = float(existing[1] or 0) if (existing[2] or 'Debit') == 'Debit' else -float(existing[1] or 0)
            total = round(t_signed + signed, 2)
            cursor.execute(
                "UPDATE ledger_opening_balances SET opening_balance = %s, opening_balance_type = %s WHERE id = %s",
                (abs(total), 'Debit' if total >= 0 else 'Credit', existing[0])
            )
            cursor.execute(
                "DELETE FROM ledger_opening_balances WHERE company_id = %s AND ledger_code = %s AND location_name = 'Main Location'",
                (company_id, code)
            )
        else:
            cursor.execute(
                "UPDATE ledger_opening_balances SET location_name = %s WHERE company_id = %s AND ledger_code = %s AND location_name = 'Main Location'",
                (target, company_id, code)
            )

    # Item opening balances: merge quantities (weighted-average the price).
    cursor.execute("""
        SELECT item_code, quantity, unit_price, opening_date
        FROM item_opening_balances
        WHERE company_id = %s AND location_name = 'Main Location'
    """, (company_id,))
    for code, qty, price, odate in cursor.fetchall():
        qty = float(qty or 0)
        price = float(price or 0)
        cursor.execute("""
            SELECT id, quantity, unit_price FROM item_opening_balances
            WHERE company_id = %s AND item_code = %s AND location_name = %s
        """, (company_id, code, target))
        existing = cursor.fetchone()
        if existing:
            t_qty = float(existing[1] or 0)
            t_price = float(existing[2] or 0)
            new_qty = t_qty + qty
            new_price = round(((t_qty * t_price) + (qty * price)) / new_qty, 2) if new_qty > 0 else 0
            cursor.execute(
                "UPDATE item_opening_balances SET quantity = %s, unit_price = %s WHERE id = %s",
                (new_qty, new_price, existing[0])
            )
            cursor.execute(
                "DELETE FROM item_opening_balances WHERE company_id = %s AND item_code = %s AND location_name = 'Main Location'",
                (company_id, code)
            )
        else:
            cursor.execute(
                "UPDATE item_opening_balances SET location_name = %s WHERE company_id = %s AND item_code = %s AND location_name = 'Main Location'",
                (target, company_id, code)
            )

    # Vouchers and item entries entered before locations were enabled
    cursor.execute(
        "UPDATE vouchers SET location_name = %s WHERE company_id = %s AND (location_name IS NULL OR location_name = 'Main Location')",
        (target, company_id)
    )
    cursor.execute(
        "UPDATE item_entries SET location_name = %s WHERE company_id = %s AND (location_name IS NULL OR location_name = 'Main Location')",
        (target, company_id)
    )
    cursor.execute(
        "UPDATE inventory SET opening_location_name = %s WHERE company_id = %s AND (opening_location_name IS NULL OR opening_location_name = 'Main Location')",
        (target, company_id)
    )

def add_location(location_code, location_name, address='', contact_person='', phone='', is_default=0, company_id=None):
    """Add new location/godown"""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return False, "Company ID required"

    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # If this is set as default, remove default from others
        if is_default:
            cursor.execute("UPDATE locations SET is_default = 0 WHERE company_id = %s", (company_id,))
        
        cursor.execute("""
            INSERT INTO locations (company_id, location_code, location_name, address, 
                                 contact_person, phone, is_default)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (company_id, location_code, location_name, address, contact_person, phone, is_default))

        if is_default:
            _rehome_unassigned_location_data(cursor, company_id, location_name)

        conn.commit()
        return True, "Location added successfully"
    except Exception as e:
        conn.rollback()
        return False, f"Error adding location: {str(e)}"
    finally:
        conn.close()

def update_location(location_id, location_code, location_name, address='', contact_person='', phone='', is_default=0, company_id=None):
    """Update existing location"""
    if company_id is None:
        company_id = get_current_company_id()
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Verify ownership
        if company_id:
            cursor.execute("SELECT id FROM locations WHERE id = %s AND company_id = %s", (location_id, company_id))
            if not cursor.fetchone():
                 return False, "Location not found or access denied"

        # If this is set as default, remove default from others
        if is_default:
            cursor.execute("UPDATE locations SET is_default = 0 WHERE company_id = %s AND id != %s", (company_id, location_id))
        
        cursor.execute("""
            UPDATE locations 
            SET location_code = %s, location_name = %s, address = %s, 
                contact_person = %s, phone = %s, is_default = %s
            WHERE id = %s
        """, (location_code, location_name, address, contact_person, phone, is_default, location_id))

        if is_default:
            _rehome_unassigned_location_data(cursor, company_id, location_name)

        conn.commit()
        return True, "Location updated successfully"
    except Exception as e:
        conn.rollback()
        return False, f"Error updating location: {str(e)}"
    finally:
        conn.close()

def delete_location(location_id, company_id=None):
    """Soft delete location (mark as inactive)"""
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Check ownership and default status
        cursor.execute("SELECT is_default, location_name FROM locations WHERE id = %s AND company_id = %s", (location_id, company_id))
        row = cursor.fetchone()
        
        if not row:
            return False, "Location not found"
            
        if row['is_default'] == 1:
            return False, "Cannot delete default location. Set another location as default first."
        
        location_name = row['location_name']

        # Determine if vouchers exist for this location (by name)
        # Note: vouchers also need company_id check
        voucher_note = ""
        try:
            cursor.execute("SELECT COUNT(1) FROM vouchers WHERE location_name = %s AND company_id = %s", (location_name, company_id))
            count_row = cursor.fetchone()
            if count_row and count_row[0] > 0:
                voucher_note = " Location has posted vouchers; deactivated instead."
        except Exception:
            pass

        # Soft delete
        cursor.execute("UPDATE locations SET is_active = 0 WHERE id = %s", (location_id,))
        conn.commit()
        return True, "Location deactivated successfully" + voucher_note
    except Exception as e:
        conn.rollback()
        return False, f"Error deleting location: {str(e)}"
    finally:
        conn.close()

def activate_location(location_id, company_id=None):
    """Reactivate a previously inactive location"""
    if company_id is None:
        company_id = get_current_company_id()
        
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("UPDATE locations SET is_active = 1 WHERE id = %s AND company_id = %s", (location_id, company_id))
        if cursor.rowcount == 0:
            return False, "Location not found"
        conn.commit()
        return True, "Location activated successfully"
    except Exception as e:
        conn.rollback()
        return False, f"Error activating location: {str(e)}"
    finally:
        conn.close()

def get_default_location(company_id=None):
    """Get the default location"""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return None

    conn = get_connection()
    cursor = conn.cursor()
    # Prefer the flagged default; fall back to the first active location so this
    # matches what the main-menu Location Switcher displays.
    cursor.execute("""
        SELECT id, location_code, location_name, address, contact_person, phone
        FROM locations
        WHERE company_id = %s AND is_active = 1
        ORDER BY is_default DESC, location_name
        LIMIT 1
    """, (company_id,))
    location = cursor.fetchone()
    conn.close()
    return dict(location) if location else None
