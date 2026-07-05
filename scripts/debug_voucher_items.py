import sqlite3
import os

DB_PATH = r'd:/Accounting App with Import/web-accounting-app_with import v4/accounting_unified.db'
VOUCHER_NO = 'SAL-00006'

def debug_voucher():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print(f"--- Debugging {VOUCHER_NO} ---")
    
    # 1. Get Items in Voucher
    cursor.execute("SELECT item_name FROM item_entries WHERE voucher_number = ?", (VOUCHER_NO,))
    items = [row[0] for row in cursor.fetchall()]
    print(f"Items in {VOUCHER_NO}: {items}")
    
    # 2. For each item, show history
    for item in items:
        print(f"\nHistory for Item: {item}")
        cursor.execute("""
            SELECT ie.id, ie.voucher_number, v.voucher_type, ie.quantity, ie.amount, ie.running_qty, ie.running_value
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number
            WHERE ie.item_name = ?
            ORDER BY ie.id
        """, (item,))
        history = cursor.fetchall()
        
        prev_val = 0.0
        for r in history:
            vid, vn, vt, qty, amt, rq, rv = r
            change = rv - prev_val
            # Highlight our voucher
            marker = " ***" if vn == VOUCHER_NO else ""
            print(f"  {vn:<15} ({vt:<15}) | Qty: {qty:<6} | Amt: {amt:<8} | RunQ: {rq:<6} | RunV: {rv:<8} | Chg: {change:.2f}{marker}")
            prev_val = rv

    # 3. Show GL Entries for Voucher
    print(f"\nGL Entries for {VOUCHER_NO}:")
    cursor.execute("SELECT ledger_name, amount, type FROM ledger_entries WHERE voucher_number = ?", (VOUCHER_NO,))
    gl_entries = cursor.fetchall()
    for ln, amt, typ in gl_entries:
        print(f"  {ln:<30} | {typ:<6} | {amt}")

    conn.close()

if __name__ == "__main__":
    debug_voucher()
