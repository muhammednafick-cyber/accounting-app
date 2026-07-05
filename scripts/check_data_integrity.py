
import sqlite3
import os
import math

DB_PATH = os.path.join(os.path.abspath("."), "Talab_Mart.db")

def check_integrity():
    print(f"Connecting to {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get all vouchers
    cursor.execute("SELECT voucher_number, voucher_type, amount, narration FROM vouchers")
    vouchers = cursor.fetchall()
    
    mismatches = []
    
    print(f"Checking {len(vouchers)} vouchers...")
    
    for v in vouchers:
        v_num, v_type, v_amount, v_narration = v
        
        # Get sum of DEBIT entries
        cursor.execute("SELECT SUM(amount) FROM ledger_entries WHERE voucher_number = ? AND type = 'Debit'", (v_num,))
        dr_sum = cursor.fetchone()[0] or 0.0
        
        # Get sum of CREDIT entries
        cursor.execute("SELECT SUM(amount) FROM ledger_entries WHERE voucher_number = ? AND type = 'Credit'", (v_num,))
        cr_sum = cursor.fetchone()[0] or 0.0
        
        # Round to 2 decimals
        v_amount = round(v_amount, 2) if v_amount else 0.0
        dr_sum = round(dr_sum, 2)
        cr_sum = round(cr_sum, 2)
        
        # Check for DR/CR imbalance
        if dr_sum != cr_sum:
            print(f"[IMBALANCE] {v_num}: Dr {dr_sum} != Cr {cr_sum}")
            
        # Check Header vs Entries
        # For simple vouchers, Header Amount should roughly equal Dr Sum (or Cr Sum).
        # (might differ slightly if there are multiple lines, but usually Total = Sum Side)
        if v_amount != dr_sum:
            mismatches.append({
                "voucher": v_num,
                "header": v_amount,
                "entries_sum": dr_sum,
                "narration": v_narration
            })

    if mismatches:
        print("\n--- Header vs Entries Mismatches ---")
        for m in mismatches:
            print(f"{m['voucher']}: Header {m['header']} != Entries {m['entries_sum']} | Narration: {m['narration']}")
    else:
        print("\nNo Header vs Entries mismatches found.")

    conn.close()

if __name__ == "__main__":
    check_integrity()
