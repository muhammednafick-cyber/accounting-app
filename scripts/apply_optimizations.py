import os
import sqlite3
import sys

# Ensure we can find DB files relative to script
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

def apply_optimizations(db_path):
    print(f"Optimizing database: {db_path}")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Enable WAL
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA cache_size=-64000;")
        
        # Create Indexes
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_vouchers_date ON vouchers(date)",
            "CREATE INDEX IF NOT EXISTS idx_vouchers_type ON vouchers(voucher_type)",
            "CREATE INDEX IF NOT EXISTS idx_vouchers_number ON vouchers(voucher_number)",
            "CREATE INDEX IF NOT EXISTS idx_ledger_entries_ledger ON ledger_entries(ledger_name)",
            "CREATE INDEX IF NOT EXISTS idx_ledger_entries_voucher ON ledger_entries(voucher_number)",
            "CREATE INDEX IF NOT EXISTS idx_item_entries_item ON item_entries(item_name)",
            "CREATE INDEX IF NOT EXISTS idx_item_entries_voucher ON item_entries(voucher_number)",
            "CREATE INDEX IF NOT EXISTS idx_item_entries_type ON item_entries(type)",
        ]
        
        for idx_sql in indexes:
            try:
                cursor.execute(idx_sql)
            except sqlite3.Error as e:
                # Some tables might not exist if DB is partial
                print(f"  Warning applying index: {e}")

        conn.commit()
        conn.close()
        print("  ✓ Success")
    except Exception as e:
        print(f"  ✗ Failed: {e}")

def main():
    print("Searching for .db files in", BASE_DIR)
    for root, dirs, files in os.walk(BASE_DIR):
        for file in files:
            if file.endswith(".db"):
                db_path = os.path.join(root, file)
                apply_optimizations(db_path)

if __name__ == "__main__":
    main()
