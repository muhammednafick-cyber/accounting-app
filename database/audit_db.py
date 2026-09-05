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


def get_audit_trail(voucher_number=None, from_date=None, to_date=None,
                    action=None, username=None, limit=200, offset=0,
                    company_id=None):
    """A page of audit entries, newest first, plus the total that matched.

    Returns (rows, total). Bounded on purpose: the trail grows by one row per
    voucher change and never shrinks, so an unpaged version of this would one
    day try to build a hundred thousand dictionaries to render one screen.
    """
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return [], 0

    where, params = ["company_id = %s"], [company_id]
    if voucher_number:
        where.append("voucher_number ILIKE %s")
        params.append(f"%{voucher_number}%")
    if from_date:
        where.append("created_at >= %s")
        params.append(f"{from_date} 00:00:00")
    if to_date:
        where.append("created_at <= %s")
        params.append(f"{to_date} 23:59:59")
    if action:
        where.append("action = %s")
        params.append(action)
    if username:
        where.append("username ILIKE %s")
        params.append(f"%{username}%")
    clause = " AND ".join(where)

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM audit_trail WHERE {clause}", tuple(params))
        total = cursor.fetchone()[0] or 0

        cursor.execute(
            f"""SELECT id, voucher_number, action, username, details, created_at
                  FROM audit_trail WHERE {clause}
                 ORDER BY created_at DESC, id DESC
                 LIMIT %s OFFSET %s""",
            tuple(params + [int(limit), int(offset)]))
        rows = [{"id": r[0], "voucher_number": r[1] or "", "action": r[2] or "",
                 "username": r[3] or "", "details": r[4] or "", "created_at": r[5]}
                for r in cursor.fetchall()]
        return rows, total
    except Exception as exc:
        print(f"get_audit_trail failed: {exc}")
        return [], 0
    finally:
        conn.close()


def get_audit_actions(company_id=None):
    """The distinct actions recorded, for the report's filter."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT action FROM audit_trail "
                       "WHERE company_id = %s AND action IS NOT NULL ORDER BY 1",
                       (company_id,))
        return [r[0] for r in cursor.fetchall()]
    finally:
        conn.close()
