import sys
import os
import psycopg2

# Add parent directory to path to import database modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from accounting_app import get_db_connection

def check_inventory():
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # 1. Check Inventory Ledger
        print("--- Inventory Ledger ---")
        cur.execute("SELECT ledger_name, group_code, closing_balance, opening_balance FROM ledgers WHERE ledger_name = 'Inventory'")
        row = cur.fetchone()
        if row:
            print(f"Ledger Found: {row}")
            group_code = row[1]
            
            # 2. Check Group
            print("\n--- Group Details ---")
            cur.execute("SELECT group_name, nature FROM groups WHERE group_code = %s", (group_code,))
            group = cur.fetchone()
            print(f"Group: {group}")
        else:
            print("Ledger 'Inventory' NOT FOUND")
            
        # 3. Check Opening Vouchers
        print("\n--- Opening Vouchers ---")
        cur.execute("SELECT voucher_number, date, amount FROM vouchers WHERE voucher_type = 'Opening'")
        vouchers = cur.fetchall()
        print(f"Found {len(vouchers)} Opening vouchers")
        for v in vouchers:
            print(f"  {v}")
            
            # 4. Check Ledger Entries for these vouchers
            cur.execute("SELECT ledger_name, amount, type FROM ledger_entries WHERE voucher_number = %s", (v[0],))
            entries = cur.fetchall()
            print(f"    Ledger Entries: {entries}")
            
            # 5. Check Item Entries for these vouchers
            cur.execute("SELECT item_name, quantity, amount FROM item_entries WHERE voucher_number = %s", (v[0],))
            items = cur.fetchall()
            print(f"    Item Entries: {len(items)} items found")
            if len(items) > 0:
                print(f"      Sample: {items[0]}")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    check_inventory()
