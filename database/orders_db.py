"""Sales and Purchase Orders.

An order is a commitment, not a transaction: nothing is posted to the ledger
or to stock when one is raised. It is worked off by the Sales or Purchase
vouchers billed against it, and its status follows from how much of each line
has been billed - Open, Partially Billed, or Closed. An order can also be
closed by hand when the balance will never arrive (short supply, cancelled
remainder), or cancelled outright while nothing has been billed.
"""
from datetime import datetime

from .config import get_connection
from .company_db import get_current_company_id
from .financial_year_db import get_fy_by_date
from .audit_db import get_current_username

ORDER_TYPES = ("Sales Order", "Purchase Order")

# What the order becomes once a voucher is raised against it
VOUCHER_TYPE_FOR_ORDER = {
    "Sales Order": "Sales",
    "Purchase Order": "Purchase",
}
ORDER_TYPE_FOR_VOUCHER = {v: k for k, v in VOUCHER_TYPE_FOR_ORDER.items()}

_PREFIX = {"Sales Order": "SO", "Purchase Order": "PO"}

STATUS_OPEN = "Open"
STATUS_PARTIAL = "Partially Billed"
STATUS_CLOSED = "Closed"
STATUS_CANCELLED = "Cancelled"


def _row(row):
    return dict(row) if row is not None else None


def _rows(rows):
    return [dict(r) for r in rows]


def next_order_number(cursor, company_id, order_type, date):
    """FY-prefixed running number, matching how vouchers are numbered."""
    fy = get_fy_by_date(date, company_id=company_id)
    if not fy:
        raise Exception(
            f"No Financial Year defined for date {date}. "
            f"Please create a Financial Year first.")
    prefix = f"FY{str(fy['start_date'])[2:4]}-{_PREFIX[order_type]}"
    cursor.execute(
        "SELECT order_number FROM orders WHERE company_id = %s "
        "AND order_number LIKE %s "
        "ORDER BY length(order_number) DESC, order_number DESC LIMIT 1",
        (company_id, f"{prefix}-%"))
    row = cursor.fetchone()
    seq = 1
    if row:
        last = row['order_number'] if hasattr(row, 'keys') else row[0]
        try:
            seq = int(str(last).split('-')[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    return f"{prefix}-{str(seq).zfill(6)}"


def create_order(order_type, date, party_ledger_name, items, reference=None,
                 expected_date=None, narration='', location_name=None,
                 company_id=None, db_connection=None):
    """Raise one order. `items` is [{item_name, quantity, unit_price}].

    Returns the new order number.
    """
    if order_type not in ORDER_TYPES:
        raise Exception(f"Unknown order type '{order_type}'")
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required")
    if not party_ledger_name:
        raise Exception("Party Ledger Name is required")

    clean = []
    for item in items or []:
        name = (item.get('item_name') or '').strip()
        if not name:
            continue
        quantity = float(item.get('quantity') or 0)
        if quantity <= 0:
            raise Exception(f"Quantity must be greater than 0 for '{name}'")
        clean.append({
            'item_name': name,
            'quantity': quantity,
            'unit_price': float(item.get('unit_price') or 0),
        })
    if not clean:
        raise Exception("An order needs at least one item line")

    conn = db_connection or get_connection()
    should_close = db_connection is None
    try:
        cursor = conn.cursor()
        # An order names a location for the same reason a voucher does - it is
        # raised for one branch and billed there.
        from .vouchers_db import resolve_voucher_location
        location_name = resolve_voucher_location(
            cursor, company_id, order_type, location_name)

        order_number = next_order_number(cursor, company_id, order_type, date)
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("""
            INSERT INTO orders (company_id, order_number, order_type, date,
                                party_ledger_name, reference, expected_date,
                                narration, location_name, status, created_by,
                                created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (company_id, order_number, order_type, date, party_ledger_name,
              reference, expected_date, narration, location_name, STATUS_OPEN,
              get_current_username(), now))
        row = cursor.fetchone()
        order_id = row['id'] if hasattr(row, 'keys') else row[0]

        for item in clean:
            cursor.execute("""
                INSERT INTO order_items (company_id, order_id, item_name,
                                         quantity, unit_price, billed_quantity)
                VALUES (%s, %s, %s, %s, %s, 0)
            """, (company_id, order_id, item['item_name'], item['quantity'],
                  item['unit_price']))

        if should_close:
            conn.commit()
        return order_number
    except Exception:
        if should_close:
            conn.rollback()
        raise
    finally:
        if should_close:
            conn.close()


# Delivery-date filters. Only orders with something still to bill can be due:
# a closed or cancelled order is nobody's problem, whatever its expected date.
DUE_FILTERS = {
    "today": "Due today",
    "overdue": "Overdue",
    "week": "Due in the next 7 days",
    "undated": "No expected date",
}


def get_orders(order_type=None, status=None, party=None, search=None,
               due=None, company_id=None, limit=500):
    """Order headers with their billed progress, newest first.

    `due` filters on the expected date - see DUE_FILTERS.
    """
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    where = ["o.company_id = %s"]
    params = [company_id]
    if order_type:
        where.append("o.order_type = %s")
        params.append(order_type)
    if status:
        where.append("o.status = %s")
        params.append(status)
    if party:
        where.append("o.party_ledger_name = %s")
        params.append(party)
    if search:
        where.append("(o.order_number ILIKE %s OR o.party_ledger_name ILIKE %s "
                     "OR COALESCE(o.reference,'') ILIKE %s)")
        like = f"%{search}%"
        params += [like, like, like]

    if due in DUE_FILTERS:
        # expected_date is stored as text, so it is cast for comparison.
        where.append("o.status IN (%s, %s)")
        params += [STATUS_OPEN, STATUS_PARTIAL]
        if due == "undated":
            where.append("(o.expected_date IS NULL OR o.expected_date = '')")
        else:
            where.append("COALESCE(o.expected_date, '') <> ''")
            if due == "today":
                where.append("o.expected_date::date = CURRENT_DATE")
            elif due == "overdue":
                where.append("o.expected_date::date < CURRENT_DATE")
            elif due == "week":
                where.append("o.expected_date::date "
                             "BETWEEN CURRENT_DATE AND CURRENT_DATE + 7")

    conn = get_connection()
    try:
        cursor = conn.cursor()
        order_by = ("o.expected_date ASC, o.order_number" if due in DUE_FILTERS
                    else "o.date DESC, o.order_number DESC")
        cursor.execute(f"""
            SELECT o.*,
                   COALESCE(SUM(oi.quantity), 0) AS ordered_quantity,
                   COALESCE(SUM(oi.billed_quantity), 0) AS billed_quantity,
                   COALESCE(SUM(oi.quantity * oi.unit_price), 0) AS order_value,
                   COUNT(oi.id) AS line_count
            FROM orders o
            LEFT JOIN order_items oi
                   ON oi.order_id = o.id AND oi.company_id = o.company_id
            WHERE {' AND '.join(where)}
            GROUP BY o.id
            ORDER BY {order_by}
            LIMIT %s
        """, tuple(params) + (limit,))
        return _rows(cursor.fetchall())
    finally:
        conn.close()


def get_order(order_number, company_id=None, with_billings=True):
    """One order: header, lines (with billed and pending quantities), and the
    vouchers raised against it."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return None

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM orders WHERE company_id = %s AND order_number = %s",
            (company_id, order_number))
        header = _row(cursor.fetchone())
        if not header:
            return None

        cursor.execute(
            "SELECT * FROM order_items WHERE company_id = %s AND order_id = %s "
            "ORDER BY id", (company_id, header['id']))
        items = _rows(cursor.fetchall())
        for item in items:
            item['pending_quantity'] = round(
                float(item['quantity']) - float(item['billed_quantity'] or 0), 4)

        billings = []
        if with_billings:
            cursor.execute("""
                SELECT b.voucher_number, b.quantity, b.created_at,
                       oi.item_name, v.date
                FROM order_billings b
                JOIN order_items oi ON oi.id = b.order_item_id
                LEFT JOIN vouchers v ON v.company_id = b.company_id
                                    AND v.voucher_number = b.voucher_number
                WHERE b.company_id = %s AND b.order_id = %s
                ORDER BY b.id
            """, (company_id, header['id']))
            billings = _rows(cursor.fetchall())

        header['items'] = items
        header['billings'] = billings
        header['ordered_quantity'] = round(
            sum(float(i['quantity']) for i in items), 4)
        header['billed_quantity'] = round(
            sum(float(i['billed_quantity'] or 0) for i in items), 4)
        header['pending_quantity'] = round(
            header['ordered_quantity'] - header['billed_quantity'], 4)
        header['order_value'] = round(
            sum(float(i['quantity']) * float(i['unit_price'] or 0)
                for i in items), 2)
        return header
    finally:
        conn.close()


def get_pending_lines(order_number, company_id=None):
    """The lines still to be billed, for prefilling a voucher.

    A line that has been billed in full is left out; nothing is returned for an
    order that is closed or cancelled.
    """
    order = get_order(order_number, company_id=company_id, with_billings=False)
    if not order:
        return None
    if order['status'] in (STATUS_CLOSED, STATUS_CANCELLED):
        return {**order, 'items': []}
    order['items'] = [i for i in order['items'] if i['pending_quantity'] > 0]
    return order


def _refresh_status(cursor, company_id, order_id):
    """Set the order's status from what has actually been billed.

    A hand-closed or cancelled order is left alone - someone decided it was
    finished, and a later voucher should not quietly reopen it.
    """
    cursor.execute(
        "SELECT status FROM orders WHERE company_id = %s AND id = %s",
        (company_id, order_id))
    row = cursor.fetchone()
    if not row:
        return None
    current = row['status'] if hasattr(row, 'keys') else row[0]
    if current == STATUS_CANCELLED:
        return current

    cursor.execute(
        "SELECT COALESCE(SUM(quantity),0), COALESCE(SUM(billed_quantity),0) "
        "FROM order_items WHERE company_id = %s AND order_id = %s",
        (company_id, order_id))
    row = cursor.fetchone()
    ordered = float(row[0] or 0)
    billed = float(row[1] or 0)

    if billed <= 0:
        status = STATUS_OPEN
    elif billed + 0.0001 >= ordered:
        status = STATUS_CLOSED
    else:
        status = STATUS_PARTIAL

    # Re-opening a hand-closed order only happens if its billing was undone.
    if current == STATUS_CLOSED and status != STATUS_CLOSED and billed <= 0:
        status = STATUS_OPEN

    cursor.execute(
        "UPDATE orders SET status = %s WHERE company_id = %s AND id = %s",
        (status, company_id, order_id))
    return status


def record_billing(cursor, company_id, voucher_number, allocations):
    """Bill quantities against order lines, inside the voucher's transaction.

    `allocations` is [{order_item_id, quantity}]. Billing more than was ordered
    is allowed - short of it the order stays Partially Billed - so this only
    records what happened and lets the status follow.
    """
    if not allocations:
        return []
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    touched = set()
    for allocation in allocations:
        item_id = allocation.get('order_item_id')
        quantity = float(allocation.get('quantity') or 0)
        if not item_id or quantity <= 0:
            continue
        cursor.execute(
            "SELECT order_id FROM order_items WHERE company_id = %s AND id = %s",
            (company_id, item_id))
        row = cursor.fetchone()
        if not row:
            continue
        order_id = row['order_id'] if hasattr(row, 'keys') else row[0]
        cursor.execute(
            "UPDATE order_items SET billed_quantity = billed_quantity + %s "
            "WHERE company_id = %s AND id = %s", (quantity, company_id, item_id))
        cursor.execute("""
            INSERT INTO order_billings (company_id, order_id, order_item_id,
                                        voucher_number, quantity, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (company_id, order_id, item_id, voucher_number, quantity, now))
        touched.add(order_id)

    return [(order_id, _refresh_status(cursor, company_id, order_id))
            for order_id in touched]


def release_billing(cursor, company_id, voucher_number):
    """Hand quantities back to their orders when a voucher is deleted."""
    cursor.execute(
        "SELECT order_id, order_item_id, quantity FROM order_billings "
        "WHERE company_id = %s AND voucher_number = %s",
        (company_id, voucher_number))
    rows = cursor.fetchall()
    if not rows:
        return []
    touched = set()
    for row in rows:
        if hasattr(row, 'keys'):
            order_id, item_id, quantity = row['order_id'], row['order_item_id'], row['quantity']
        else:
            order_id, item_id, quantity = row[0], row[1], row[2]
        cursor.execute(
            "UPDATE order_items SET billed_quantity = "
            "GREATEST(billed_quantity - %s, 0) WHERE company_id = %s AND id = %s",
            (float(quantity), company_id, item_id))
        touched.add(order_id)
    cursor.execute(
        "DELETE FROM order_billings WHERE company_id = %s AND voucher_number = %s",
        (company_id, voucher_number))
    return [(order_id, _refresh_status(cursor, company_id, order_id))
            for order_id in touched]


def close_order(order_number, reason='', company_id=None):
    """Close what is left of an order by hand - the balance is not coming."""
    return _set_status(order_number, STATUS_CLOSED, reason, company_id)


def cancel_order(order_number, reason='', company_id=None):
    """Cancel an order outright. Only while nothing has been billed."""
    return _set_status(order_number, STATUS_CANCELLED, reason, company_id,
                       require_unbilled=True)


def reopen_order(order_number, company_id=None):
    """Undo a manual close or cancel, restoring the billed-based status."""
    if company_id is None:
        company_id = get_current_company_id()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM orders WHERE company_id = %s AND order_number = %s",
            (company_id, order_number))
        row = cursor.fetchone()
        if not row:
            raise Exception(f"Order {order_number} not found")
        order_id = row['id'] if hasattr(row, 'keys') else row[0]
        cursor.execute(
            "UPDATE orders SET status = %s, status_reason = NULL "
            "WHERE company_id = %s AND id = %s", (STATUS_OPEN, company_id, order_id))
        status = _refresh_status(cursor, company_id, order_id)
        conn.commit()
        return status
    finally:
        conn.close()


def _set_status(order_number, status, reason, company_id, require_unbilled=False):
    if company_id is None:
        company_id = get_current_company_id()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, status FROM orders WHERE company_id = %s AND order_number = %s",
            (company_id, order_number))
        row = cursor.fetchone()
        if not row:
            raise Exception(f"Order {order_number} not found")
        order_id = row['id'] if hasattr(row, 'keys') else row[0]

        if require_unbilled:
            cursor.execute(
                "SELECT COALESCE(SUM(billed_quantity),0) FROM order_items "
                "WHERE company_id = %s AND order_id = %s", (company_id, order_id))
            billed = float(cursor.fetchone()[0] or 0)
            if billed > 0:
                raise Exception(
                    f"{order_number} has already been billed and cannot be "
                    f"cancelled. Close it instead to write off the balance.")

        cursor.execute(
            "UPDATE orders SET status = %s, status_reason = %s "
            "WHERE company_id = %s AND id = %s",
            (status, reason or None, company_id, order_id))
        conn.commit()
        return status
    finally:
        conn.close()


def delete_order(order_number, company_id=None):
    """Remove an order entirely. Refused once anything has been billed."""
    if company_id is None:
        company_id = get_current_company_id()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM orders WHERE company_id = %s AND order_number = %s",
            (company_id, order_number))
        row = cursor.fetchone()
        if not row:
            raise Exception(f"Order {order_number} not found")
        order_id = row['id'] if hasattr(row, 'keys') else row[0]
        cursor.execute(
            "SELECT COUNT(*) FROM order_billings WHERE company_id = %s AND order_id = %s",
            (company_id, order_id))
        if int(cursor.fetchone()[0] or 0) > 0:
            raise Exception(
                f"{order_number} has vouchers billed against it and cannot be "
                f"deleted. Close it instead.")
        cursor.execute("DELETE FROM order_items WHERE company_id = %s AND order_id = %s",
                       (company_id, order_id))
        cursor.execute("DELETE FROM orders WHERE company_id = %s AND id = %s",
                       (company_id, order_id))
        conn.commit()
        return True
    finally:
        conn.close()


def get_open_orders_for_party(order_type, party_ledger_name, company_id=None):
    """Orders a voucher could be billed against: right type, right party, and
    something still pending."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT o.order_number, o.date, o.reference, o.status,
                   COALESCE(SUM(oi.quantity - oi.billed_quantity), 0) AS pending_quantity
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.id AND oi.company_id = o.company_id
            WHERE o.company_id = %s AND o.order_type = %s
              AND o.party_ledger_name = %s AND o.status IN (%s, %s)
            GROUP BY o.id
            HAVING COALESCE(SUM(oi.quantity - oi.billed_quantity), 0) > 0
            ORDER BY o.date DESC, o.order_number DESC
        """, (company_id, order_type, party_ledger_name,
              STATUS_OPEN, STATUS_PARTIAL))
        return _rows(cursor.fetchall())
    finally:
        conn.close()
