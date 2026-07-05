import sqlite3
import os

DB_PATH = os.path.join(os.path.abspath("."), "Talab_Mart.db")

def check_balance_theory():
    print(f"Connecting to {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check AL AIN FARMS balance BEFORE the first test voucher (2026-02-03)
    cursor.execute("""
        SELECT 
            SUM(CASE WHEN le.type = 'Debit' THEN le.amount ELSE 0 END) as total_dr,
            SUM(CASE WHEN le.type = 'Credit' THEN le.amount ELSE 0 END) as total_cr
        FROM ledger_entries le
        JOIN vouchers v ON le.voucher_number = v.voucher_number
        WHERE le.ledger_name = 'AL AIN FARMS FOR LIVESTOCK PRODUCTION'
        AND v.date < '2026-02-03'
    """)
    row = cursor.fetchone()
    dr_before, cr_before = row[0] or 0, row[1] or 0
    balance_before = cr_before - dr_before  # Credit balance is positive for suppliers
    
    print(f"\nAL AIN FARMS balance before 2026-02-03:")
    print(f"  Total Debit (before):  {dr_before:.2f}")
    print(f"  Total Credit (before): {cr_before:.2f}")
    print(f"  Net Balance (Cr-Dr):   {balance_before:.2f}")
    
    print(f"\nTheory Check:")
    print(f"  User entered: 5000")
    print(f"  Balance before: {balance_before:.2f}")
    print(f"  Expected if added: {5000 + balance_before:.2f}")
    print(f"  Actual in PAY-00001: 6000.00")
    print(f"  Actual in REC-00001: 7000.00")
    
    print(f"\nDoes 5000 + balance = actual amounts?")
    print(f"  PAY-00001: 5000 + {balance_before:.2f} = {5000 + balance_before:.2f} vs 6000.00 → {abs(5000 + balance_before - 6000) < 1}")
    print(f"  REC-00001: 5000 + {balance_before:.2f} = {5000 + balance_before:.2f} vs 7000.00 → {abs(5000 + balance_before - 7000) < 1}")

    conn.close()

if __name__ == "__main__":
    check_balance_theory()
