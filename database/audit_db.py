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
                    action=None, voucher_type=None, voucher_from=None,
                    voucher_to=None, limit=200, offset=0, company_id=None):
    """A page of audit entries, newest first, plus the total that matched.

    Two different dates are on offer and they answer different questions:

      from_date / to_date       when the change was made (audit_trail.created_at)
      voucher_from / voucher_to the voucher's own date (vouchers.date)

    A voucher dated last December and entered today appears under today for the
    first and under December for the second.

    The voucher's type and date come from a LEFT JOIN, so an entry whose
    voucher has since been deleted still appears - with no type or date, since
    the row it described is gone. Filtering by type or voucher date therefore
    cannot include those; the details column still records what they were.
    """
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return [], 0

    where, params = ["a.company_id = %s"], [company_id]
    if voucher_number:
        where.append("a.voucher_number ILIKE %s")
        params.append(f"%{voucher_number}%")
    if from_date:
        where.append("a.created_at >= %s")
        params.append(f"{from_date} 00:00:00")
    if to_date:
        where.append("a.created_at <= %s")
        params.append(f"{to_date} 23:59:59")
    if action:
        where.append("a.action = %s")
        params.append(action)
    if voucher_type:
        where.append("v.voucher_type = %s")
        params.append(voucher_type)
    if voucher_from:
        where.append("v.date >= %s")
        params.append(voucher_from)
    if voucher_to:
        where.append("v.date <= %s")
        params.append(voucher_to)
    clause = " AND ".join(where)

    joined = ("FROM audit_trail a "
              "LEFT JOIN vouchers v ON v.voucher_number = a.voucher_number "
              "AND v.company_id = a.company_id "
              f"WHERE {clause}")

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) {joined}", tuple(params))
        total = cursor.fetchone()[0] or 0

        cursor.execute(
            f"""SELECT a.id, a.voucher_number, a.action, a.username, a.details,
                       a.created_at, v.voucher_type, v.date, v.amount
                  {joined}
                 ORDER BY a.created_at DESC, a.id DESC
                 LIMIT %s OFFSET %s""",
            tuple(params + [int(limit), int(offset)]))
        rows = [{"id": r[0], "voucher_number": r[1] or "", "action": r[2] or "",
                 "username": r[3] or "", "details": r[4] or "", "created_at": r[5],
                 "voucher_type": r[6] or "", "voucher_date": r[7] or "",
                 "amount": r[8]}
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


def get_audited_voucher_types(company_id=None):
    """Voucher types that appear in the trail, for the report's dropdown."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT DISTINCT v.voucher_type FROM audit_trail a
                 JOIN vouchers v ON v.voucher_number = a.voucher_number
                                AND v.company_id = a.company_id
                WHERE a.company_id = %s AND v.voucher_type IS NOT NULL
                ORDER BY 1""", (company_id,))
        return [r[0] for r in cursor.fetchall()]
    finally:
        conn.close()
