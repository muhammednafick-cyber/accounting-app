import sqlite3
import json
from .config import get_connection
from .company_db import get_current_company_id

def _initialize_voucher_config_table():
    """Initialize voucher configuration table"""
    # Moved to unified_db.init_unified_db
    pass

def get_voucher_config(voucher_type, side, company_id=None):
    """
    Get config for a specific voucher type and side.
    Returns dict: {'allowed_groups': [], 'allowed_sub_groups': []} or None if not set.
    """
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT allowed_groups, allowed_sub_groups FROM voucher_type_configs WHERE voucher_type = ? AND side = ? AND company_id = ?",
            (voucher_type, side, company_id)
        )
        row = cursor.fetchone()
        if row:
            return {
                "allowed_groups": json.loads(row[0]),
                "allowed_sub_groups": json.loads(row[1])
            }
        return None
    except Exception as e:
        print(f"Error getting voucher config: {e}")
        return None
    finally:
        conn.close()

def save_voucher_config(voucher_type, side, allowed_groups, allowed_sub_groups, company_id=None):
    """
    Save or update config.
    allowed_groups: list of strings (group codes)
    allowed_sub_groups: list of integers (sub group ids)
    """
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    try:
        json_groups = json.dumps(allowed_groups)
        json_sub_groups = json.dumps(allowed_sub_groups)
        
        cursor.execute(
            """
            INSERT INTO voucher_type_configs (company_id, voucher_type, side, allowed_groups, allowed_sub_groups)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(company_id, voucher_type, side) DO UPDATE SET
            allowed_groups = excluded.allowed_groups,
            allowed_sub_groups = excluded.allowed_sub_groups
            """,
            (company_id, voucher_type, side, json_groups, json_sub_groups)
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error saving voucher config: {e}")
        raise e
    finally:
        conn.close()

def get_allowed_ledgers(voucher_type, side, company_id=None):
    """
    Returns a list of allowed ledger dicts for a given voucher type and side.
    If no config exists, returns all ledgers (default behavior).
    """
    if company_id is None:
        company_id = get_current_company_id()

    config = get_voucher_config(voucher_type, side, company_id=company_id)
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        if not config:
            # Default behavior: All ledgers allowed for this company
            cursor.execute("SELECT ledger_code, ledger_name, group_code, sub_group_id FROM ledgers WHERE company_id = ?", (company_id,))
            rows = cursor.fetchall()
            return [{"code": r[0], "name": r[1], "group_code": r[2], "sub_group_id": r[3]} for r in rows]

        allowed_groups = config.get("allowed_groups", [])
        allowed_sub_groups = config.get("allowed_sub_groups", [])
        
        # Build query dynamically
        query = "SELECT ledger_code, ledger_name, group_code, sub_group_id FROM ledgers WHERE company_id = ? AND ("
        conditions = []
        params = [company_id]
        
        if allowed_groups:
            placeholders = ",".join("?" * len(allowed_groups))
            conditions.append(f"group_code IN ({placeholders})")
            params.extend(allowed_groups)
            
        if allowed_sub_groups:
            placeholders = ",".join("?" * len(allowed_sub_groups))
            conditions.append(f"sub_group_id IN ({placeholders})")
            params.extend(allowed_sub_groups)
            
        if not conditions:
            # Config exists but strictly empty? Usually means nothing allowed.
            return []
            
        query += " OR ".join(conditions) + ")"
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [{"code": r[0], "name": r[1], "group_code": r[2], "sub_group_id": r[3]} for r in rows]
        
    except Exception as e:
        print(f"Error getting allowed ledgers: {e}")
        return []
    finally:
        conn.close()
