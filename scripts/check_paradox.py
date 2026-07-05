import sqlite3
import os

DB_PATH = r'd:/Accounting App with Import/web-accounting-app_with import v4/accounting_unified.db'

def check_paradox():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Inventory Table
    cursor.execute("SELECT SUM(stock_value) FROM inventory")
    inv_table_sum = cursor.fetchone()[0] or 0.0
    
    # 2. GL Entries
    cursor.execute("SELECT SUM(CASE WHEN type='Debit' THEN amount ELSE -amount END) FROM ledger_entries WHERE ledger_name IN (SELECT ledger_name FROM ledgers WHERE ledger_name LIKE '%Inventory%' OR ledger_name LIKE '%Stock%')")
    gl_sum = cursor.fetchone()[0] or 0.0
    
    # 3. Item Entries Sum (Change in Running Value)
    cursor.execute("""
        SELECT ie.item_name, ie.voucher_number, ie.running_value, v.date, v.voucher_id 
        FROM item_entries ie
        JOIN vouchers v ON ie.voucher_number = v.voucher_number
        ORDER BY ie.item_name, v.date ASC, v.voucher_id ASC, ie.id ASC
    """)
    entries = cursor.fetchall()
    
    item_entries_sum = 0.0
    current_item = None
    prev_val = 0.0
    
    for item, vn, rv, dt, vid in entries:
        if item != current_item:
            current_item = item
            prev_val = 0.0
        
        change = rv - prev_val
        prev_val = rv
        item_entries_sum += change
        
    print(f"Inventory Table Sum: {inv_table_sum}")
    print(f"GL Entries Sum: {gl_sum}")
    print(f"Item Entries Calculated Sum: {item_entries_sum}")
    
    conn.close()

if __name__ == "__main__":
    check_paradox()
