import sqlite3
import os

DB_PATH = os.path.join(os.path.abspath("."), "Talab_Mart.db")

def check_ledger_balances():
    print(f"Connecting to {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    ledgers_to_check = ['Cash', 'Bank Account', 'AL AIN FARMS FOR LIVESTOCK PRODUCTION', 'Salary Payables', 'Salary Expenses']
    
    for ledger in ledgers_to_check:
        print(f"\n{'='*60}")
        print(f"Ledger: {ledger}")
        print('='*60)
        
        cursor.execute("SELECT ledger_name, opening_balance, opening_balance_type, closing_balance FROM ledgers WHERE ledger_name = ?", (ledger,))
        row = cursor.fetchone()
        
        if not row:
            print("NOT FOUND")
            continue
            
        print(f"Opening Balance: {row[1]} {row[2] or ''}")
        print(f"Closing Balance: {row[3]}")
        
        # Check all transactions for this ledger
        print("\nTransactions:")
        cursor.execute("""
            SELECT le.voucher_number, v.date, le.amount, le.type
            FROM ledger_entries le
            JOIN vouchers v ON le.voucher_number = v.voucher_number
            WHERE le.ledger_name = ?
            ORDER BY v.date, v.voucher_id
        """, (ledger,))
        
        transactions = cursor.fetchall()
        for v_no, date, amt, typ in transactions:
            print(f"  {date} | {v_no:12} | {typ:6} | {amt:10.2f}")

    conn.close()

if __name__ == "__main__":
    check_ledger_balances()
