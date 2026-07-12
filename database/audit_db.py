from datetime import datetime

from .config import get_connection
from .company_db import get_current_company_id


def get_current_username():
    """Best-effort resolution of the logged-in user; 'system' outside a request."""
    try:
        from flask_login import current_user
        if current_user and getattr(current_user, "is_authenticated", False):
            return current_user.username
    except Exception:
        pass
    return "system"


def log_audit(action, voucher_number=None, details="", company_id=None, db_connection=None, username=None):
    """Record an audit trail row. Never raises — auditing must not break the transaction flow."""
    try:
        if company_id is None:
            company_id = get_current_company_id()
        if not company_id:
            return
        if username is None:
            username = get_current_username()

        if db_connection:
            conn = db_connection
            should_close = False
        else:
            conn = get_connection()
            should_close = True
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO audit_trail (company_id, voucher_number, action, username, details, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (company_id, voucher_number, action, username, details,
                 datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            )
            if should_close:
                conn.commit()
        finally:
            if should_close:
                conn.close()
    except Exception as e:
        print(f"log_audit failed ({action} {voucher_number}): {e}")


def get_audit_trail(voucher_number=None, from_date=None, to_date=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    try:
        cursor = conn.cursor()
        sql = """
            SELECT voucher_number, action, username, details, created_at
            FROM audit_trail
            WHERE company_id = %s
        """
        params = [company_id]
        if voucher_number:
            sql += " AND voucher_number = %s"
            params.append(voucher_number)
        if from_date:
            sql += " AND created_at >= %s"
            params.append(f"{from_date} 00:00:00")
        if to_date:
            sql += " AND created_at <= %s"
            params.append(f"{to_date} 23:59:59")
        sql += " ORDER BY created_at DESC, id DESC"
        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()
        return [
            {
                "voucher_number": r[0] if not hasattr(r, 'keys') else r['voucher_number'],
                "action": r[1] if not hasattr(r, 'keys') else r['action'],
                "username": r[2] if not hasattr(r, 'keys') else r['username'],
                "details": r[3] if not hasattr(r, 'keys') else r['details'],
                "created_at": str(r[4] if not hasattr(r, 'keys') else r['created_at']),
            }
            for r in rows
        ]
    finally:
        conn.close()
