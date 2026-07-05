
import sqlite3
import os
import json

DB_PATH = os.path.join(os.path.abspath("."), "Talab_Mart.db")

def check_config():
    print(f"Connecting to {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # 1. Check Config for Receipt
        print("\n--- Receipt Config ---")
        cursor.execute("SELECT * FROM voucher_type_configs WHERE voucher_type = 'Receipt'")
        rows = cursor.fetchall()
        if not rows:
            print("No config found for Receipt.")
        else:
            for r in rows:
                # id, type, side, allowed_groups, allowed_sub_groups
                print(f"Side: {r[2]}")
                print(f"Allowed Groups: {r[3]}")
        
        # 2. Check Cash Ledger
        print("\n--- Cash Ledger ---")
        cursor.execute("SELECT ledger_name, group_code, sub_group_id FROM ledgers WHERE ledger_name = 'Cash'")
        cash = cursor.fetchone()
        if cash:
            print(f"Name: {cash[0]}, Group: {cash[1]}, SubGroup: {cash[2]}")
            # Get Group Name
            cursor.execute("SELECT group_name FROM groups WHERE group_code = ?", (cash[1],))
            grp = cursor.fetchone()
            print(f"Group Name: {grp[0] if grp else 'UNKNOWN'}")
        else:
            print("Ledger 'Cash' not found.")
            
    except Exception as e:
        print(f"Error: {e}")

    conn.close()

if __name__ == "__main__":
    check_config()
