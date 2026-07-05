
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.config import get_connection

def diagnose_again():
    print("--- Diagnostic Report ---")
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # 1. Company
        cursor.execute("SELECT id, name FROM companies")
        companies = cursor.fetchall()
        print(f"Companies: {companies}")
        if not companies:
            print("No companies.")
            return
        cid = companies[0][0]
        
        # 2. Financial Years
        print("\n--- Financial Years ---")
        cursor.execute("SELECT * FROM financial_years WHERE company_id = %s", (cid,))
        fys = cursor.fetchall()
        for fy in fys:
            print(f"FY: {fy}")
            
        # 3. Inventory Master
        print("\n--- Inventory Master (Count & Sample) ---")
        cursor.execute("SELECT COUNT(*) FROM inventory WHERE company_id = %s", (cid,))
        count = cursor.fetchone()[0]
        print(f"Total Items: {count}")
        
        cursor.execute("SELECT name, stock_quantity, stock_value, opening_price FROM inventory WHERE company_id = %s AND stock_quantity > 0 LIMIT 5", (cid,))
        items = cursor.fetchall()
        for i in items:
            print(f"Item: {i}")

        # 4. Opening Vouchers
        print("\n--- ALL Vouchers ---")
        cursor.execute("SELECT id, voucher_number, voucher_type, date, amount, company_id FROM vouchers")
        vouchers = cursor.fetchall()
        print(f"Total Vouchers: {len(vouchers)}")
        for v in vouchers:
            print(f"Voucher: {v}")
            
        # 4b. Item Entries
        print("\n--- ALL Item Entries ---")
        cursor.execute("SELECT id, voucher_number, item_name, quantity, amount, company_id FROM item_entries")
        ientries = cursor.fetchall()
        print(f"Total Item Entries: {len(ientries)}")
        for ie in ientries[:10]:
            print(f"ItemEntry: {ie}")

        # 5. Ledger Entries
        print("\n--- Ledger Entries (Inventory) ---")
        try:
            cursor.execute("""
                SELECT le.ledger_name, le.type, le.amount, v.date, v.voucher_number
                FROM ledger_entries le
                JOIN vouchers v ON le.voucher_number = v.voucher_number
                WHERE v.company_id = %s AND le.ledger_name = 'Inventory'
            """, (cid,))
            lent = cursor.fetchall()
            print(f"Total Inventory Ledger Entries: {len(lent)}")
            for le in lent:
                print(f"Entry: {le}")
        except Exception as e:
            print(f"Error reading ledger entries: {e}")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    diagnose_again()
