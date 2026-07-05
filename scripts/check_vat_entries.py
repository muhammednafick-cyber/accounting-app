import sqlite3
import os

DB_PATH = os.path.join(os.path.abspath("."), "Talab_Mart.db")

def check_vat():
    print(f"Connecting to {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    vouchers_to_check = ['PAY-00001', 'REC-00001', 'CON-00001', 'EXP-00001']
    
    for v_num in vouchers_to_check:
        print(f"\n{'='*60}")
        print(f"Voucher: {v_num}")
        print('='*60)
        
        # Get header
        cursor.execute("SELECT voucher_type, amount, narration FROM vouchers WHERE voucher_number = ?", (v_num,))
        header = cursor.fetchone()
        if not header:
            print("NOT FOUND")
            continue
            
        print(f"Type: {header[0]}")
        print(f"Header Amount: {header[1]}")
        print(f"Narration: {header[2]}")
        
        # Get all ledger entries
        print("\nLedger Entries:")
        cursor.execute("SELECT ledger_name, amount, type FROM ledger_entries WHERE voucher_number = ? ORDER BY type, ledger_name", (v_num,))
        entries = cursor.fetchall()
        
        total_dr = 0
        total_cr = 0
        has_vat = False
        
        for ledger, amt, typ in entries:
            print(f"  {typ:6} | {ledger:30} | {amt:10.2f}")
            if typ == 'Debit':
                total_dr += amt
            else:
                total_cr += amt
            if 'VAT' in ledger.upper():
                has_vat = True
                
        print(f"\nTotal Debit:  {total_dr:.2f}")
        print(f"Total Credit: {total_cr:.2f}")
        print(f"Has VAT Entry: {has_vat}")
        print(f"Difference from Header: {total_dr - header[1]:.2f}")

    conn.close()

if __name__ == "__main__":
    check_vat()
