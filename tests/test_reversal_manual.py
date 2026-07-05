
import sqlite3
import os
import sys
from datetime import datetime

# Add parent directory to path to import modules
sys.path.append(os.path.abspath("d:/Accounting App with Import/web-accounting-app_with import v6"))

# Initialize DB path
from database.config import get_connection
from database.vouchers_db import add_voucher, get_voucher_details
from accounting_app.voucher_routes import api_create_reversal

# Mocking Flask request context is hard, so we will test the LOGIC by calling add_voucher manually 
# and then manually triggering the reversal logic that matches what api_create_reversal does.
# Or better, we can just use the functions from voucher_routes if we refactor, but for now
# I will simulate the logic in this script to verify the DB behavior.

def test_reversal_logic():
    print("Testing Reversal Logic...")
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Create a dummy FY for testing
        cursor.execute("INSERT OR IGNORE INTO financial_years (fy_code, start_date, end_date, is_locked, is_active) VALUES ('FY-TEST', '2020-01-01', '2030-12-31', 0, 1)")
        
        # Create dummy Ledgers
        cursor.execute("INSERT OR IGNORE INTO ledgers (ledger_code, ledger_name, group_code, opening_balance, opening_balance_type, closing_balance) VALUES ('L-TEST-01', 'Cash', 'G005', 0, 'Debit', 0)")
        cursor.execute("INSERT OR IGNORE INTO ledgers (ledger_code, ledger_name, group_code, opening_balance, opening_balance_type, closing_balance) VALUES ('L-TEST-02', 'Sales Account', 'G001', 0, 'Credit', 0)")
        
        conn.commit()

        # 1. Create a dummy Receipt voucher
        today = datetime.today().strftime('%Y-%m-%d')
        ledger_entries = [
            {'ledger_name': 'Cash', 'amount': 100.0, 'type': 'Debit'},
            {'ledger_name': 'Sales Account', 'amount': 100.0, 'type': 'Credit'}
        ]
        
        # Determine next voucher number manually to track it
        cursor.execute("SELECT COUNT(*) FROM vouchers WHERE voucher_type = 'Receipt'")
        count = cursor.fetchone()[0]
        v_num = f"REC-TEST-{count+1}"
        
        # We use add_voucher directly
        original_v_num = add_voucher(
            voucher_type="Receipt",
            date=today,
            ledger_entries=ledger_entries,
            item_entries=[],
            narration="Test Original Voucher"
        )
        print(f"Created Original Voucher: {original_v_num} (Type: Receipt)")
        
        # 2. Simulate Reversal Logic (as implemented in api_create_reversal)
        # Fetch the voucher
        original_voucher = get_voucher_details(original_v_num)
        
        voucher_type = original_voucher["header"]["voucher_type"]
        
        new_ledger_entries = []
        for le in original_voucher.get("ledger_entries", []):
            new_type = "Credit" if le["type"] == "Debit" else "Debit"
            new_ledger_entries.append({
                "ledger_name": le["ledger_name"],
                "amount": le["amount"],
                "type": new_type,
                "cost_center_code": le.get("cost_center_code")
            })
            
        reversal_narration = f"Reversal of {original_v_num}"
        
        reversal_v_num = add_voucher(
            voucher_type=voucher_type, # Should be Receipt
            date=today,
            ledger_entries=new_ledger_entries,
            item_entries=[],
            narration=reversal_narration,
            linked_voucher_number=original_v_num
        )
        print(f"Created Reversal Voucher: {reversal_v_num}")
        
        # 3. Verify
        cursor.execute("SELECT voucher_type, linked_voucher_number FROM vouchers WHERE voucher_number = ?", (reversal_v_num,))
        row = cursor.fetchone()
        
        if not row:
            print("FAILED: Reversal voucher not found in DB.")
        else:
            rev_type, linked = row
            print(f"Reversal Voucher Type: {rev_type}")
            print(f"Linked Voucher: {linked}")
            
            if rev_type == "Receipt" and linked == original_v_num:
                print("SUCCESS: Reversal has correct Type and Link.")
            else:
                print(f"FAILED: Expected Type 'Receipt', got '{rev_type}'. Expected Link '{original_v_num}', got '{linked}'.")

        # Verify Entries
        cursor.execute("SELECT ledger_name, type, amount FROM ledger_entries WHERE voucher_number = ?", (reversal_v_num,))
        entries = cursor.fetchall()
        print("Reversal Entries:")
        for e in entries:
            print(f" - {e[0]}: {e[1]} {e[2]}")
            # Check against original
            # Cash was Debit 100, should be Credit 100
            # Sales was Credit 100, should be Debit 100
            if e[0] == 'Cash' and e[1] != 'Credit':
                print(f"FAILED: Cash should be Credit, got {e[1]}")
            if e[0] == 'Sales Account' and e[1] != 'Debit':
                 print(f"FAILED: Sales Account should be Debit, got {e[1]}")

        # Clean up test data?
        # Maybe leave it for inspection or delete it.
        # Let's delete it to keep DB clean-ish
        # cursor.execute("DELETE FROM ledger_entries WHERE voucher_number IN (?, ?)", (original_v_num, reversal_v_num))
        # cursor.execute("DELETE FROM vouchers WHERE voucher_number IN (?, ?)", (original_v_num, reversal_v_num))
        # conn.commit()
        # print("Cleanup test data.")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

if __name__ == "__main__":
    test_reversal_logic()
