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
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

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
        
        # We can simulate INSERT OR UPDATE
        # Postgres: ON CONFLICT (company_id, item_code) DO UPDATE ... OR (company_id, name)
        # But we have two potential unique constraints (code and name) which makes ON CONFLICT tricky if we don't know which one violated.
        # But `add_inventory` logic here seems to try insert, and if fail, update.
        # Let's try INSERT and catch exception to UPDATE.
        
        try:
            cursor.execute("""
                INSERT INTO inventory
                (company_id, item_code, name, stock_group_code, unit_code, unit_price, stock_quantity, vat_rate)
                VALUES (%s, %s, %s, %s, %s, %s, 0, %s)
            """, (company_id, item_code, name, stock_group_code, unit_code, unit_price, vat_rate))
            if should_close:
                conn.commit()
            print(f"Added inventory: {item_code} {name} Price={unit_price} VAT={vat_rate} (Company: {company_id})")
        except Exception as e:
            # Check if integrity error
            conn.rollback() 
            cursor.execute(
                """
                UPDATE inventory
                SET stock_group_code = %s, unit_code = %s, unit_price = %s, vat_rate = %s
                WHERE company_id = %s AND (name = %s OR item_code = %s)
                """,
                (stock_group_code, unit_code, unit_price, vat_rate, company_id, name, item_code)
            )
            if cursor.rowcount == 0:
                # If update matched no rows, it means the item didn't exist, 
                # so the original INSERT failure was likely a real error (e.g. invalid foreign key).
                raise e
                
            if should_close:
                conn.commit()
            print(f"Updated inventory: {item_code} {name} Price={unit_price} VAT={vat_rate} (Company: {company_id})")
    finally:
        if should_close:
            conn.close()

def add_inventory(item_code, name, stock_group_code, unit_code, unit_price, vat_rate=0, db_connection=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")

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
        
        # We can simulate INSERT OR UPDATE
        # Postgres: ON CONFLICT (company_id, item_code) DO UPDATE ... OR (company_id, name)
        # But we have two potential unique constraints (code and name) which makes ON CONFLICT tricky if we don't know which one violated.
        # But `add_inventory` logic here seems to try insert, and if fail, update.
        # Let's try INSERT and catch exception to UPDATE.
        
        try:
            cursor.execute("""
                INSERT INTO inventory
                (company_id, item_code, name, stock_group_code, unit_code, unit_price, stock_quantity, vat_rate)
                VALUES (%s, %s, %s, %s, %s, %s, 0, %s)
            """, (company_id, item_code, name, stock_group_code, unit_code, unit_price, vat_rate))
            if should_close:
                conn.commit()
            print(f"Added inventory: {item_code} {name} Price={unit_price} VAT={vat_rate} (Company: {company_id})")
        except Exception as e:
            # Check if integrity error
            conn.rollback() 
            cursor.execute(
                """
                UPDATE inventory
                SET stock_group_code = %s, unit_code = %s, unit_price = %s, vat_rate = %s
                WHERE company_id = %s AND (name = %s OR item_code = %s)
                """,
                (stock_group_code, unit_code, unit_price, vat_rate, company_id, name, item_code)
            )
            if cursor.rowcount == 0:
                # If update matched no rows, it means the item didn't exist, 
                # so the original INSERT failure was likely a real error (e.g. invalid foreign key).
                raise e
                
            if should_close:
                conn.commit()
            print(f"Updated inventory: {item_code} {name} Price={unit_price} VAT={vat_rate} (Company: {company_id})")
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
        existing_names = set(row[1] for row in rows)
        
        inserts = []
        updates = []
        
        for item in items:
            code = item['item_code']
            name = item['name']
            
            if code in existing_codes or name in existing_names:
                updates.append((
                    item['stock_group_code'], 
                    item['unit_code'], 
                    item['unit_price'], 
                    item['vat_rate'], 
                    company_id, 
                    name, 
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
                existing_names.add(name)
        
        if updates:
            cursor.executemany("""
                UPDATE inventory
                SET stock_group_code = %s, unit_code = %s, unit_price = %s, vat_rate = %s
                WHERE company_id = %s AND (name = %s OR item_code = %s)
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
                i.opening_location_name
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

def get_items(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT item_code, name FROM inventory WHERE company_id = %s", (company_id,))
    items = cursor.fetchall()
    conn.close()
    return [dict(i) for i in items]

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
        cursor.execute("DELETE FROM inventory WHERE company_id = %s AND name = %s", (company_id, item_name))
        if cursor.rowcount == 0:
            raise Exception("Item not found.")
        conn.commit()
        print(f"delete_inventory: {item_name} (Company: {company_id})")
    except Exception as e:
        raise Exception(f"Error deleting inventory item: {str(e)}")
    finally:
        conn.close()
