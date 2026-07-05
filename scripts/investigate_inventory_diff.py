import sqlite3
import os

DB_PATH = r'd:/Accounting App with Import/web-accounting-app_with import v4/accounting_unified.db'

def get_connection():
    return sqlite3.connect(DB_PATH)

def investigate():
    conn = get_connection()
    cursor = conn.cursor()

    print("--- Inventory Ledger(s) ---")
    # Find Inventory Ledger by searching for 'Inventory' or 'Stock' in Assets
    cursor.execute("""
        SELECT l.ledger_name, l.closing_balance, g.nature 
        FROM ledgers l
        JOIN groups g ON l.group_code = g.group_code AND l.company_id = g.company_id
         WHERE (l.ledger_name LIKE '%Inventory%' OR l.ledger_name LIKE '%Stock%')
         AND g.nature = 'Assets'
    """)
    ledgers = cursor.fetchall()
    
    inventory_ledger_name = None
    
    if not ledgers:
        cursor.execute("SELECT ledger_name FROM ledgers WHERE ledger_name = 'Inventory'")
        res = cursor.fetchone()
        if res:
             inventory_ledger_name = res[0]
    else:
        for name, bal, nature in ledgers:
            if name == 'Inventory':
                inventory_ledger_name = name
    
    if not inventory_ledger_name and ledgers:
        inventory_ledger_name = ledgers[0][0]
    
    if not inventory_ledger_name:
        print("Cannot proceed without identifying Inventory ledger.")
        return

    print(f"Using Inventory Ledger: {inventory_ledger_name}")

    # 1. GL Balance
    cursor.execute("""
        SELECT SUM(CASE WHEN type='Debit' THEN amount ELSE -amount END)
        FROM ledger_entries
        WHERE ledger_name = ?
    """, (inventory_ledger_name,))
    gl_total = cursor.fetchone()[0] or 0.0
    print(f"Total GL Balance: {gl_total}")

    # 2. Inventory Value
    cursor.execute("SELECT SUM(stock_value) FROM inventory")
    inv_total = cursor.fetchone()[0] or 0.0
    print(f"Total Inventory Value: {inv_total}")

    diff = inv_total - gl_total
    print(f"Difference (Inventory - GL): {diff}")

    # 3. GL Voucher Movements
    cursor.execute("""
        SELECT voucher_number, SUM(CASE WHEN type='Debit' THEN amount ELSE -amount END)
        FROM ledger_entries
        WHERE ledger_name = ?
        GROUP BY voucher_number
    """, (inventory_ledger_name,))
    gl_movements = {row[0]: row[1] for row in cursor.fetchall()}

    # 4. Item Voucher Cost Impacts
    # We must calculate the CHANGE in running_value for each item transaction
    cursor.execute("SELECT item_name, voucher_number, running_value FROM item_entries ORDER BY item_name, id")
    entries = cursor.fetchall()
    
    item_voucher_impacts = {} # voucher_number -> total cost impact
    
    current_item = None
    prev_val = 0.0
    
    for item, vn, rv in entries:
        if item != current_item:
            current_item = item
            prev_val = 0.0 # Reset for new item
            # Assume starting value is 0 unless we have history. 
            # In 'recreated db', earliest entry is start.
            
        change = rv - prev_val
        prev_val = rv
        
        if vn not in item_voucher_impacts: item_voucher_impacts[vn] = 0.0
        item_voucher_impacts[vn] += change

    # 5. Comparison
    print("\n--- Discrepancy Analysis ---")
    all_vouchers = set(gl_movements.keys()) | set(item_voucher_impacts.keys())
    
    found_diff = False
    
    print(f"{'Voucher':<15} | {'Type':<15} | {'GL Net':<10} | {'Item Net':<10} | {'Diff':<10}")
    print("-" * 70)
    
    for vn in sorted(all_vouchers):
        gl_val = gl_movements.get(vn, 0.0)
        item_val = item_voucher_impacts.get(vn, 0.0)
        
        if abs(gl_val - item_val) > 0.01:
            # Fetch Type
            cursor.execute("SELECT voucher_type FROM vouchers WHERE voucher_number = ?", (vn,))
            res = cursor.fetchone()
            vtype = res[0] if res else "Unknown"
            
            print(f"{vn:<15} | {vtype:<15} | {round(gl_val, 2):<10} | {round(item_val, 2):<10} | {round(gl_val - item_val, 2):<10}")
            found_diff = True

    if not found_diff:
        print("No voucher-level discrepancies found.")

    conn.close()

if __name__ == "__main__":
    investigate()
