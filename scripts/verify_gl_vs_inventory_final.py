import sqlite3
import os

DB_PATH = r'd:/Accounting App with Import/web-accounting-app_with import v4/accounting_unified.db'

def verify():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("--- Verifying GL vs Inventory (Date Ordered) ---")

    # 1. GL Movements per Voucher
    print("Fetching GL movements...")
    cursor.execute("""
        SELECT voucher_number, SUM(CASE WHEN type='Debit' THEN amount ELSE -amount END)
        FROM ledger_entries
        WHERE ledger_name IN (SELECT ledger_name FROM ledgers WHERE ledger_name LIKE '%Inventory%' OR ledger_name LIKE '%Stock%') -- Assumption
        GROUP BY voucher_number
    """)
    gl_movements = {row[0]: row[1] for row in cursor.fetchall()}
    
    # Verify Inventory Ledger Name
    cursor.execute("SELECT ledger_name FROM ledgers WHERE ledger_name = 'Inventory'")
    if not cursor.fetchone():
        print("WARNING: 'Inventory' ledger not found specifically. Using broad search.")

    # 2. Inventory Movements per Voucher (Calculated from Running Value Difference in Date Order)
    print("Fetching Item entries...")
    # Fetch all entries sorted exactly as recalculate_running_balance processes them
    cursor.execute("""
        SELECT ie.item_name, ie.voucher_number, ie.running_value, v.voucher_type 
        FROM item_entries ie
        JOIN vouchers v ON ie.voucher_number = v.voucher_number
        ORDER BY ie.item_name, v.date ASC, v.voucher_id ASC, ie.id ASC
    """)
    entries = cursor.fetchall()
    
    inv_movements = {}
    
    current_item = None
    prev_val = 0.0
    
    for item, vn, rv, vt in entries:
        if item != current_item:
            current_item = item
            prev_val = 0.0
            
        change = rv - prev_val
        prev_val = rv
        
        if vn not in inv_movements: inv_movements[vn] = 0.0
        inv_movements[vn] += change

    # 3. Compare
    all_vouchers = set(gl_movements.keys()) | set(inv_movements.keys())
    
    print(f"{'Voucher':<15} | {'GL Net':<15} | {'Inv Net':<15} | {'Diff':<15}")
    print("-" * 65)
    
    total_diff = 0.0
    found_any = False
    
    for vn in sorted(all_vouchers):
        gl = gl_movements.get(vn, 0.0)
        inv = inv_movements.get(vn, 0.0)
        
        diff = inv - gl # Inv is Net Change. GL is Debit-Credit.
        # Check signs.
        # Purchase: Inv Increases (+). GL Debit (+). Match.
        # Sales: Inv Decreases (-). GL Credit (-). Match.
        
        # Note: rounding issues
        if abs(diff) > 0.01:
            print(f"{vn:<15} | {round(gl, 2):<15} | {round(inv, 2):<15} | {round(diff, 2):<15}")
            found_any = True
            total_diff += diff

    print("-" * 65)
    print(f"Total Discrepancy Sum: {round(total_diff, 2)}")

    conn.close()

if __name__ == "__main__":
    verify()
