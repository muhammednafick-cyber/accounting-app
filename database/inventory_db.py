"""
Inventory module: Inventory groups, Units, Items
"""
# import sqlite3 - removed
from .config import get_connection
from .company_db import get_current_company_id

def ensure_default_units(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return

    conn = get_connection()
    cursor = conn.cursor()
    default_units = [
        ('NOS', 'Numbers'),
        ('KG', 'Kilogram'),
        ('LTR', 'Litre'),
        ('BOX', 'Box'),
    ]
    
    # Insert with company_id
    data_to_insert = [(company_id, code, name) for code, name in default_units]
    
    cursor.executemany(
        "INSERT INTO units (company_id, unit_code, unit_name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        data_to_insert
    )
    conn.commit()
    conn.close()

# ========== Inventory Groups ==========
def get_inventory_groups(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT group_code, group_name FROM inventory_groups WHERE company_id = %s", (company_id,))
    groups = cursor.fetchall()
    conn.close()
    return [dict(g) for g in groups]

def add_inventory_group(group_code, group_name, db_connection=None, company_id=None):
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
            "INSERT INTO inventory_groups (company_id, group_code, group_name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (company_id, group_code, group_name)
        )
        if should_close:
            conn.commit()
        # Rowcount might be 0 if conflict ignored
        print(f"add_inventory_group: {group_code}, {group_name} (Company: {company_id})")
    except Exception as e:
        raise Exception(f"Error adding inventory group: {str(e)}")
    finally:
        if should_close:
            conn.close()

def delete_inventory_group(group_name, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM inventory WHERE company_id = %s AND stock_group_code = (SELECT group_code FROM inventory_groups WHERE company_id = %s AND group_name = %s)",
            (company_id, company_id, group_name)
        )
        if cursor.fetchone()[0] > 0:
            raise Exception("Cannot delete inventory group: Items exist under this group.")
        cursor.execute("DELETE FROM inventory_groups WHERE company_id = %s AND group_name = %s", (company_id, group_name))
        if cursor.rowcount == 0:
            raise Exception("Inventory group not found.")
        conn.commit()
        print(f"delete_inventory_group: {group_name} (Company: {company_id})")
    except Exception as e:
        raise Exception(f"Error deleting inventory group: {str(e)}")
    finally:
        conn.close()

# ========== Units ==========
def get_units(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT unit_code, unit_name FROM units WHERE company_id = %s", (company_id,))
    units = cursor.fetchall()
    conn.close()
    return [dict(u) for u in units]

def add_unit(unit_code, unit_name, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO units (company_id, unit_code, unit_name) VALUES (%s, %s, %s)",
            (company_id, unit_code, unit_name)
        )
        conn.commit()
    except Exception as e:
        # Postgres might raise IntegrityError
        raise Exception(f"Error adding unit: {str(e)}")
    finally:
        conn.close()

def delete_unit(unit_code, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM inventory WHERE company_id = %s AND unit_code = %s",
            (company_id, unit_code)
        )
        if cursor.fetchone()[0] > 0:
            raise Exception("Cannot delete unit: Items exist with this unit.")
        cursor.execute("DELETE FROM units WHERE company_id = %s AND unit_code = %s", (company_id, unit_code))
        if cursor.rowcount == 0:
            raise Exception("Unit not found.")
        conn.commit()
    except Exception as e:
        raise Exception(f"Error deleting unit: {str(e)}")
    finally:
        conn.close()

# ========== Inventory Items ==========
def add_inventory(item_code, name, stock_group_code, unit_code, unit_price, vat_rate=0, db_connection=None, company_id=None):
    """Create an item, or update it when the same item (by code or name) already
    exists. Rejects a *different* item whose name matches an existing one
    ignoring case/spacing — item names must be unique per company."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

    item_code = str(item_code).strip()
    name = str(name).strip()
    unit_price = round(float(unit_price), 2)
    vat_rate = round(float(vat_rate), 2)

    if db_connection:
        conn = db_connection
        should_close = False
    else:
        conn = get_connection()
        should_close = True

    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT item_code, name FROM inventory WHERE company_id = %s AND (item_code = %s OR LOWER(TRIM(name)) = LOWER(%s))",
            (company_id, item_code, name)
        )
        rows = cursor.fetchall()
        by_code = next((r for r in rows if r[0] == item_code), None)
        by_name = next((r for r in rows if r[0] != item_code), None)

        if by_name is not None:
            # Another item (different code) already uses this name
            raise Exception(
                f"Item name '{name}' already exists as '{by_name[1]}' (code {by_name[0]}). Item names must be unique."
            )

        if by_code is not None:
            cursor.execute(
                """
                UPDATE inventory
                SET name = %s, stock_group_code = %s, unit_code = %s, unit_price = %s, vat_rate = %s
                WHERE company_id = %s AND item_code = %s
                """,
                (name, stock_group_code, unit_code, unit_price, vat_rate, company_id, item_code)
            )
            action = "Updated"
        else:
            cursor.execute("""
                INSERT INTO inventory
                (company_id, item_code, name, stock_group_code, unit_code, unit_price, stock_quantity, vat_rate)
                VALUES (%s, %s, %s, %s, %s, %s, 0, %s)
            """, (company_id, item_code, name, stock_group_code, unit_code, unit_price, vat_rate))
            action = "Added"

        if should_close:
            conn.commit()
        print(f"{action} inventory: {item_code} {name} Price={unit_price} VAT={vat_rate} (Company: {company_id})")
    except Exception:
        if should_close:
            conn.rollback()
        raise
    finally:
        if should_close:
            conn.close()

def add_inventory_batch(items, company_id=None, db_connection=None):
    """
    Batch add or update inventory items.
    items: List of dicts with keys: item_code, name, stock_group_code, unit_code, unit_price, vat_rate
    """
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")
        
    if not items:
        return

    if db_connection:
        conn = db_connection
        should_close = False
    else:
        conn = get_connection()
        should_close = True
        
    cursor = conn.cursor()
    try:
        # 1. Fetch existing items to determine Insert vs Update
        # We need to check both item_code and name
        codes = set(i['item_code'] for i in items)
        names = set(i['name'] for i in items)
        
        existing_map = {} # Key: (code, name) or code or name? 
        # Easier to just fetch all for company if list is huge, or filter by IN clause
        
        # Safe way with IN clause
        # Note: If list is massive, IN clause might be too big. But for import queue (few thousands), it's fine.
        # Actually, let's just select item_code, name from inventory
        
        cursor.execute("SELECT item_code, name FROM inventory WHERE company_id = %s", (company_id,))
        rows = cursor.fetchall()
        existing_codes = set(row[0] for row in rows)
        # Names compared case/space-insensitively: item names must be unique
        existing_names = set((row[1] or '').strip().lower() for row in rows)

        inserts = []
        updates = []
        batch_names = {}  # normalized name -> item_code (duplicates within the file)

        for item in items:
            code = str(item['item_code']).strip()
            name = str(item['name']).strip()
            norm = name.lower()

            if norm in batch_names and batch_names[norm] != code:
                raise Exception(
                    f"Duplicate item name in file: '{name}' appears with codes '{batch_names[norm]}' and '{code}'. Item names must be unique."
                )
            batch_names[norm] = code

            if code in existing_codes or norm in existing_names:
                # Existing item (by code or by name) -> update, never duplicate
                updates.append((
                    item['stock_group_code'],
                    item['unit_code'],
                    item['unit_price'],
                    item['vat_rate'],
                    company_id,
                    norm,
                    code
                ))
            else:
                inserts.append((
                    company_id,
                    code,
                    name,
                    item['stock_group_code'],
                    item['unit_code'],
                    item['unit_price'],
                    0, # stock_quantity
                    item['vat_rate']
                ))
                # Add to set to prevent duplicates within the batch if user uploads duplicates
                existing_codes.add(code)
                existing_names.add(norm)

        if updates:
            cursor.executemany("""
                UPDATE inventory
                SET stock_group_code = %s, unit_code = %s, unit_price = %s, vat_rate = %s
                WHERE company_id = %s AND (LOWER(TRIM(name)) = %s OR item_code = %s)
            """, updates)
            
        if inserts:
            cursor.executemany("""
                INSERT INTO inventory
                (company_id, item_code, name, stock_group_code, unit_code, unit_price, stock_quantity, vat_rate)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, inserts)
            
        if should_close:
            conn.commit()
            
        print(f"Batch Inventory: Inserted {len(inserts)}, Updated {len(updates)} (Company: {company_id})")
            
    except Exception as e:
        if should_close:
            conn.rollback()
        raise e
    finally:
        if should_close:
            conn.close()

def get_item_opening_balances(item_code=None, company_id=None):
    """Per-location opening stock rows. If item_code is None, returns all for the company."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if item_code:
            cursor.execute("""
                SELECT item_code, location_name, quantity, unit_price, opening_date
                FROM item_opening_balances WHERE company_id = %s AND item_code = %s
                ORDER BY location_name
            """, (company_id, item_code))
        else:
            cursor.execute("""
                SELECT item_code, location_name, quantity, unit_price, opening_date
                FROM item_opening_balances WHERE company_id = %s
                ORDER BY item_code, location_name
            """, (company_id,))
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

def upsert_item_opening_balance(item_code, location_name, quantity, unit_price, opening_date=None, company_id=None, db_connection=None):
    """Record the opening stock of an item for a location (overwrite same
    location, add for a new location)."""
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
            INSERT INTO item_opening_balances
                (company_id, item_code, location_name, quantity, unit_price, opening_date)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (company_id, item_code, location_name)
            DO UPDATE SET quantity = EXCLUDED.quantity,
                          unit_price = EXCLUDED.unit_price,
                          opening_date = EXCLUDED.opening_date
        """, (company_id, item_code, location_name or 'Main Location',
              round(float(quantity or 0), 4), round(float(unit_price or 0), 2), opening_date))
        if should_close:
            conn.commit()
    finally:
        if should_close:
            conn.close()

def get_inventory_details(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                i.item_code,
                i.name,
                i.stock_group_code,
                ig.group_name,
                i.unit_code,
                i.unit_price,
                i.stock_quantity,
                i.vat_rate,
                i.opening_price,
                i.opening_location_name,
                COALESCE(i.is_active, 1) AS is_active
            FROM inventory i
            LEFT JOIN inventory_groups ig ON i.stock_group_code = ig.group_code AND i.company_id = ig.company_id
            WHERE i.company_id = %s
            ORDER BY i.name
        """, (company_id,))
        result = cursor.fetchall()
        print(f"get_inventory_details: {len(result)} items loaded")
        return [dict(r) for r in result]
    finally:
        conn.close()

def get_items(company_id=None, include_inactive=False):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    if include_inactive:
        cursor.execute("SELECT item_code, name FROM inventory WHERE company_id = %s", (company_id,))
    else:
        cursor.execute("SELECT item_code, name FROM inventory WHERE company_id = %s AND COALESCE(is_active, 1) = 1", (company_id,))
    items = cursor.fetchall()
    conn.close()
    return [dict(i) for i in items]

def set_inventory_active(item_code, is_active, company_id=None):
    """Block (is_active=0) or unblock (1) an item. Blocked items stay in
    reports and history but are hidden from new-voucher item lists."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE inventory SET is_active = %s WHERE company_id = %s AND item_code = %s",
            (1 if is_active else 0, company_id, item_code)
        )
        if cursor.rowcount == 0:
            raise Exception("Item not found.")
        conn.commit()
    finally:
        conn.close()

def delete_inventory(item_name, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM item_entries WHERE company_id = %s AND item_name = %s", (company_id, item_name))
        if cursor.fetchone()[0] > 0:
            raise Exception("Cannot delete item: Transactions exist for this item.")
        cursor.execute(
            "DELETE FROM item_opening_balances WHERE company_id = %s AND item_code = (SELECT item_code FROM inventory WHERE company_id = %s AND name = %s)",
            (company_id, company_id, item_name)
        )
        cursor.execute("DELETE FROM inventory WHERE company_id = %s AND name = %s", (company_id, item_name))
        if cursor.rowcount == 0:
            raise Exception("Item not found.")
        conn.commit()
        print(f"delete_inventory: {item_name} (Company: {company_id})")
    except Exception as e:
        raise Exception(f"Error deleting inventory item: {str(e)}")
    finally:
        conn.close()
