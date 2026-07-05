import sqlite3
import os

DB_PATH = os.path.join(os.path.abspath("."), "Talab_Mart.db")

def verify_rec_00006():
    print(f"Connecting to {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    v_num = 'REC-00006'
    
    print(f"\n{'='*60}")
    print(f"Verifying Voucher: {v_num}")
    print('='*60)
    
    # Get header
    cursor.execute("SELECT voucher_type, amount, narration, date FROM vouchers WHERE voucher_number = ?", (v_num,))
    header = cursor.fetchone()
    
    if not header:
        print("VOUCHER NOT FOUND")
        conn.close()
        return
        
    print(f"\nHEADER:")
    print(f"  Type: {header[0]}")
    print(f"  Amount: {header[1]}")
    print(f"  Date: {header[3]}")
    print(f"  Narration: {header[2]}")
    
    # Get ledger entries
    print(f"\nLEDGER ENTRIES:")
    cursor.execute("SELECT ledger_name, amount, type FROM ledger_entries WHERE voucher_number = ? ORDER BY type DESC", (v_num,))
    entries = cursor.fetchall()
    
    total_dr = 0
    total_cr = 0
    
    for ledger, amt, typ in entries:
        print(f"  {typ:6} | {ledger:30} | {amt:10.2f}")
        if typ == 'Debit':
            total_dr += amt
        else:
            total_cr += amt
    
    print(f"\n{'='*60}")
    print("VERIFICATION RESULTS:")
    print('='*60)
    
    header_amount = header[1]
    all_match = (header_amount == total_dr == total_cr == 5000.0)
    
    print(f"  Header Amount:  {header_amount:.2f}")
    print(f"  Total Debit:    {total_dr:.2f}")
    print(f"  Total Credit:   {total_cr:.2f}")
    print(f"  Expected:       5000.00")
    
    if all_match:
        print(f"\nSUCCESS! All amounts are correct (5000.00)")
        print("The chatbot fix is working properly!")
    else:
        print(f"\nMISMATCH DETECTED!")
        print(f"Header vs Entries difference: {abs(header_amount - total_dr):.2f}")

    conn.close()

if __name__ == "__main__":
    verify_rec_00006()
