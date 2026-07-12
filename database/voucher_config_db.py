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
            "SELECT allowed_groups, allowed_sub_groups FROM voucher_type_configs WHERE voucher_type = %s AND side = %s AND company_id = %s",
            (voucher_type, side, company_id)
        )
        row = cursor.fetchone()
        if row:
            groups = json.loads(row[0] or '[]')
            sub_groups = json.loads(row[1] or '[]')
            # An empty config (legacy rows) means no restriction, not "block all"
            if not groups and not sub_groups:
                return None
            return {
                "allowed_groups": groups,
                "allowed_sub_groups": sub_groups
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
        # Empty selection = no restriction: remove the config row entirely so
        # it can't silently mean "nothing allowed".
        if not allowed_groups and not allowed_sub_groups:
            cursor.execute(
                "DELETE FROM voucher_type_configs WHERE company_id = %s AND voucher_type = %s AND side = %s",
                (company_id, voucher_type, side)
            )
            conn.commit()
            return True

        json_groups = json.dumps(allowed_groups)
        json_sub_groups = json.dumps(allowed_sub_groups)

        cursor.execute(
            """
            INSERT INTO voucher_type_configs (company_id, voucher_type, side, allowed_groups, allowed_sub_groups)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (company_id, voucher_type, side) DO UPDATE SET
            allowed_groups = EXCLUDED.allowed_groups,
            allowed_sub_groups = EXCLUDED.allowed_sub_groups
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
    If no config exists, returns all (active) ledgers — default behavior.
    Blocked ledgers are always excluded.
    """
    if company_id is None:
        company_id = get_current_company_id()

    config = get_voucher_config(voucher_type, side, company_id=company_id)

    conn = get_connection()
    cursor = conn.cursor()

    try:
        active = "COALESCE(is_active, 1) = 1"
        if not config:
            # Default behavior: All active ledgers for this company
            cursor.execute(f"SELECT ledger_code, ledger_name, group_code, sub_group_id FROM ledgers WHERE company_id = %s AND {active}", (company_id,))
            rows = cursor.fetchall()
            return [{"code": r[0], "name": r[1], "group_code": r[2], "sub_group_id": r[3]} for r in rows]

        allowed_groups = config.get("allowed_groups", [])
        allowed_sub_groups = config.get("allowed_sub_groups", [])

        # Build query dynamically
        query = f"SELECT ledger_code, ledger_name, group_code, sub_group_id FROM ledgers WHERE company_id = %s AND {active} AND ("
        conditions = []
        params = [company_id]

        if allowed_groups:
            placeholders = ",".join(["%s"] * len(allowed_groups))
            conditions.append(f"group_code IN ({placeholders})")
            params.extend(allowed_groups)

        if allowed_sub_groups:
            placeholders = ",".join(["%s"] * len(allowed_sub_groups))
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
