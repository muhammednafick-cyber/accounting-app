"""Integrity checks for the books.

Every check answers one question a bookkeeper would ask before closing a
period, and returns rows you can act on rather than a bare pass/fail. Nothing
here writes to the database except `repair_cached_balances`, which is only
called when the user asks for it.
"""
from .config import get_connection
from .company_db import get_current_company_id

OK, WARN, FAIL = "ok", "warning", "fail"


def _result(key, title, status, summary, columns=None, rows=None, fix=None):
    return {"key": key, "title": title, "status": status, "summary": summary,
            "columns": columns or [], "rows": [list(r) for r in (rows or [])],
            "fix": fix}


def run_all_checks(company_id=None):
    """Every check, in the order a reviewer would want to see them."""
    company_id = company_id or get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    try:
        cursor = conn.cursor()
        checks = [
            _check_trial_balance(cursor, company_id),
            _check_cached_balances(cursor, company_id),
            _check_voucher_double_entry(cursor, company_id),
            _check_orphan_ledger_entries(cursor, company_id),
            _check_unknown_ledgers(cursor, company_id),
            _check_orphan_item_entries(cursor, company_id),
            _check_unknown_items(cursor, company_id),
            _check_vouchers_outside_financial_years(cursor, company_id),
            _check_negative_stock(cursor, company_id),
            _check_duplicate_voucher_numbers(cursor, company_id),
        ]
        return [c for c in checks if c]
    finally:
        conn.close()


def _check_trial_balance(cursor, company_id):
    cursor.execute("""
        SELECT SUM(CASE WHEN type = 'Debit' THEN amount ELSE 0 END),
               SUM(CASE WHEN type = 'Credit' THEN amount ELSE 0 END)
        FROM ledger_entries WHERE company_id = %s
    """, (company_id,))
    row = cursor.fetchone() or (0, 0)
    debit, credit = float(row[0] or 0), float(row[1] or 0)
    diff = round(debit - credit, 2)
    if abs(diff) < 0.01:
        return _result("trial_balance", "Debits equal credits", OK,
                       f"Total debits and credits both come to {debit:,.2f}.")
    return _result("trial_balance", "Debits equal credits", FAIL,
                   f"Debits {debit:,.2f} against credits {credit:,.2f} - "
                   f"out by {diff:,.2f}. Something has posted one-sided.",
                   ["Total Debit", "Total Credit", "Difference"],
                   [[round(debit, 2), round(credit, 2), diff]])


def _check_cached_balances(cursor, company_id):
    cursor.execute("""
        SELECT l.ledger_name, COALESCE(l.closing_balance, 0),
               CASE WHEN COALESCE(l.opening_balance_type,'') = 'Credit'
                    THEN -COALESCE(l.opening_balance,0)
                    ELSE COALESCE(l.opening_balance,0) END
               + COALESCE(e.movement, 0)
        FROM ledgers l
        LEFT JOIN (SELECT ledger_name,
                          SUM(CASE WHEN type='Debit' THEN amount ELSE -amount END)
                          AS movement
                   FROM ledger_entries WHERE company_id = %s
                   GROUP BY ledger_name) e
               ON e.ledger_name = l.ledger_name
        WHERE l.company_id = %s
    """, (company_id, company_id))
    drift = [[r[0], round(float(r[1] or 0), 2), round(float(r[2] or 0), 2),
              round(float(r[1] or 0) - float(r[2] or 0), 2)]
             for r in cursor.fetchall()
             if abs(float(r[1] or 0) - float(r[2] or 0)) > 0.005]
    if not drift:
        return _result("cached_balances", "Stored balances match the entries", OK,
                       "Every ledger's stored closing balance agrees with its "
                       "posted entries.")
    return _result("cached_balances", "Stored balances match the entries", WARN,
                   f"{len(drift)} ledger(s) have a stored balance that no longer "
                   "matches their entries. Reports correct this automatically, "
                   "but the stored value is stale.",
                   ["Ledger", "Stored", "From Entries", "Difference"], drift,
                   fix="repair_cached_balances")


def _check_voucher_double_entry(cursor, company_id):
    cursor.execute("""
        SELECT v.voucher_number, v.voucher_type, v.date,
               COALESCE(SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE 0 END),0),
               COALESCE(SUM(CASE WHEN le.type='Credit' THEN le.amount ELSE 0 END),0)
        FROM vouchers v
        JOIN ledger_entries le ON le.voucher_number = v.voucher_number
                              AND le.company_id = v.company_id
        WHERE v.company_id = %s
        GROUP BY v.voucher_number, v.voucher_type, v.date
        HAVING ABS(COALESCE(SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE 0 END),0)
                 - COALESCE(SUM(CASE WHEN le.type='Credit' THEN le.amount ELSE 0 END),0)) > 0.01
        ORDER BY v.date DESC
    """, (company_id,))
    rows = [[r[0], r[1], r[2], round(float(r[3]), 2), round(float(r[4]), 2),
             round(float(r[3]) - float(r[4]), 2)] for r in cursor.fetchall()]
    if not rows:
        return _result("double_entry", "Every voucher balances", OK,
                       "No voucher has debits and credits that disagree.")
    return _result("double_entry", "Every voucher balances", FAIL,
                   f"{len(rows)} voucher(s) do not balance.",
                   ["Voucher", "Type", "Date", "Debit", "Credit", "Difference"],
                   rows)


def _check_orphan_ledger_entries(cursor, company_id):
    cursor.execute("""
        SELECT le.voucher_number, le.ledger_name, le.amount, le.type
        FROM ledger_entries le
        WHERE le.company_id = %s AND NOT EXISTS (
            SELECT 1 FROM vouchers v WHERE v.company_id = le.company_id
              AND v.voucher_number = le.voucher_number)
        ORDER BY le.voucher_number
    """, (company_id,))
    rows = [list(r) for r in cursor.fetchall()]
    if not rows:
        return _result("orphan_entries", "No orphaned ledger entries", OK,
                       "Every ledger entry belongs to a voucher.")
    return _result("orphan_entries", "No orphaned ledger entries", FAIL,
                   f"{len(rows)} ledger entr(y/ies) point at a voucher that no "
                   "longer exists. These still affect balances.",
                   ["Voucher", "Ledger", "Amount", "Dr/Cr"], rows)


def _check_unknown_ledgers(cursor, company_id):
    cursor.execute("""
        SELECT DISTINCT le.ledger_name, COUNT(*)
        FROM ledger_entries le
        WHERE le.company_id = %s AND NOT EXISTS (
            SELECT 1 FROM ledgers l WHERE l.company_id = le.company_id
              AND l.ledger_name = le.ledger_name)
        GROUP BY le.ledger_name ORDER BY 2 DESC
    """, (company_id,))
    rows = [list(r) for r in cursor.fetchall()]
    if not rows:
        return _result("unknown_ledgers", "All entries use a known ledger", OK,
                       "Every posted entry names a ledger that exists.")
    return _result("unknown_ledgers", "All entries use a known ledger", FAIL,
                   f"{len(rows)} ledger name(s) are posted to but not in the "
                   "master. They will be missing from the trial balance.",
                   ["Ledger Name", "Entries"], rows)


def _check_orphan_item_entries(cursor, company_id):
    cursor.execute("""
        SELECT ie.voucher_number, ie.item_name, ie.quantity, ie.amount
        FROM item_entries ie
        WHERE ie.company_id = %s AND NOT EXISTS (
            SELECT 1 FROM vouchers v WHERE v.company_id = ie.company_id
              AND v.voucher_number = ie.voucher_number)
    """, (company_id,))
    rows = [list(r) for r in cursor.fetchall()]
    if not rows:
        return _result("orphan_items", "No orphaned item entries", OK,
                       "Every item line belongs to a voucher.")
    return _result("orphan_items", "No orphaned item entries", FAIL,
                   f"{len(rows)} item line(s) point at a missing voucher.",
                   ["Voucher", "Item", "Quantity", "Amount"], rows)


def _check_unknown_items(cursor, company_id):
    cursor.execute("""
        SELECT ie.item_name, COUNT(*)
        FROM item_entries ie
        WHERE ie.company_id = %s AND NOT EXISTS (
            SELECT 1 FROM inventory i WHERE i.company_id = ie.company_id
              AND i.name = ie.item_name)
        GROUP BY ie.item_name ORDER BY 2 DESC
    """, (company_id,))
    rows = [list(r) for r in cursor.fetchall()]
    if not rows:
        return _result("unknown_items", "All item lines use a known item", OK,
                       "Every item line names an item that exists.")
    return _result("unknown_items", "All item lines use a known item", WARN,
                   f"{len(rows)} item name(s) are used on vouchers but are not "
                   "in the item master.", ["Item Name", "Lines"], rows)


def _check_vouchers_outside_financial_years(cursor, company_id):
    cursor.execute("SELECT COUNT(*) FROM financial_years WHERE company_id = %s",
                   (company_id,))
    if not (cursor.fetchone() or [0])[0]:
        return _result("fy_coverage", "Vouchers fall inside a financial year",
                       WARN, "No financial years are defined for this company.")
    cursor.execute("""
        SELECT v.voucher_number, v.voucher_type, v.date, v.amount
        FROM vouchers v
        WHERE v.company_id = %s AND NOT EXISTS (
            SELECT 1 FROM financial_years fy
            WHERE fy.company_id = v.company_id
              AND v.date >= fy.start_date AND v.date <= fy.end_date)
        ORDER BY v.date
    """, (company_id,))
    rows = [list(r) for r in cursor.fetchall()]
    if not rows:
        return _result("fy_coverage", "Vouchers fall inside a financial year", OK,
                       "Every voucher is dated inside a defined financial year.")
    return _result("fy_coverage", "Vouchers fall inside a financial year", WARN,
                   f"{len(rows)} voucher(s) are dated outside every defined "
                   "financial year, so they may be missing from year reports.",
                   ["Voucher", "Type", "Date", "Amount"], rows)


def _check_negative_stock(cursor, company_id):
    cursor.execute("""
        SELECT item_code, name, stock_quantity
        FROM inventory WHERE company_id = %s AND stock_quantity < 0
        ORDER BY stock_quantity
    """, (company_id,))
    rows = [list(r) for r in cursor.fetchall()]
    if not rows:
        return _result("negative_stock", "No negative stock", OK,
                       "No item is showing a negative quantity.")
    return _result("negative_stock", "No negative stock", WARN,
                   f"{len(rows)} item(s) show negative stock - usually a sale "
                   "posted before its purchase.",
                   ["Code", "Item", "Quantity"], rows)


def _check_duplicate_voucher_numbers(cursor, company_id):
    cursor.execute("""
        SELECT voucher_number, COUNT(*) FROM vouchers
        WHERE company_id = %s GROUP BY voucher_number HAVING COUNT(*) > 1
    """, (company_id,))
    rows = [list(r) for r in cursor.fetchall()]
    if not rows:
        return _result("duplicate_numbers", "Voucher numbers are unique", OK,
                       "No voucher number is used twice.")
    return _result("duplicate_numbers", "Voucher numbers are unique", FAIL,
                   f"{len(rows)} voucher number(s) are used more than once. "
                   "Entries cannot be attributed reliably.",
                   ["Voucher Number", "Count"], rows)


def repair_cached_balances(company_id=None):
    """Rewrite every stored closing balance from the posted entries."""
    company_id = company_id or get_current_company_id()
    if not company_id:
        return 0
    conn = get_connection()
    try:
        cursor = conn.cursor()
        from .reports_db import verify_closing_balances
        return verify_closing_balances(cursor, conn, company_id)
    finally:
        conn.close()
