import sqlite3
import os

DB_PATH = r'd:/Accounting App with Import/web-accounting-app_with import v4/accounting_unified.db'

def check_gl():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    vouchers = ['OPEN-00001', 'PUR-00007', 'SAL-00006']
    
    print(f"Checking GL 'Inventory' entries for: {vouchers}")
    
    cursor.execute(f"""
        SELECT voucher_number, ledger_name, amount, type 
        FROM ledger_entries 
        WHERE voucher_number IN ({','.join(['?']*len(vouchers))})
        AND ledger_name IN ('Inventory')
    """, vouchers)
    
    rows = cursor.fetchall()
    
    total_gl = 0.0
    
    print(f"{'Voucher':<15} | {'Type':<8} | {'Amount':<10}")
    print("-" * 40)
    for vn, ln, amt, typ in rows:
        print(f"{vn:<15} | {typ:<8} | {amt:<10}")
        if typ == 'Debit':
            total_gl += amt
        else:
            total_gl -= amt
            
    print(f"\nNet GL Impact: {total_gl}")
    
    # Expected Inventory Impact
    # Start 9. Pur 21. Sal 15.
    # Net = 9 + 21 - 15 = 15.
    
    conn.close()

if __name__ == "__main__":
    check_gl()
