
import sqlite3
import os

DB_PATH = os.path.join(os.path.abspath("."), "default_company.db")


def check_db(db_path):
    print(f"--- Checking {db_path} ---")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vouchers'")
        if not cursor.fetchone():
            print("Table 'vouchers' does not exist.")
            conn.close()
            return

        # Check specific voucher
        cursor.execute("SELECT * FROM vouchers WHERE voucher_number = 'REV-00001'")
        row = cursor.fetchone()
        if row:
            print(f"!!! FOUND REV-00001 in {db_path} !!!")
            print(f"Row: {row}")
        else:
            print(f"REV-00001 NOT found in {db_path}")

        # Check count of Reversal types
        cursor.execute("SELECT COUNT(*) FROM vouchers WHERE voucher_type IN ('Reversal', 'REV')")
        count = cursor.fetchone()[0]
        print(f"Count of 'Reversal'/'REV' vouchers: {count}")

        conn.close()
    except Exception as e:
        print(f"Error checking {db_path}: {e}")

def investigate():
    dbs = [
        "default_company.db",
        "Talab_Mart.db",
        "database/default_company.db",
        "database/accounting.db"
    ]
    
    for db in dbs:
        path = os.path.join(os.path.abspath("."), db)
        if os.path.exists(path):
            check_db(path)
        else:
            print(f"DB not found: {path}")

if __name__ == "__main__":
    investigate()
