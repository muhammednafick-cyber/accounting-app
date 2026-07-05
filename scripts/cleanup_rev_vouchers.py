import sqlite3
import os

# Database path
DB_PATH = os.path.join(os.path.abspath("."), "Talab_Mart.db")

def cleanup_rev_vouchers():
    print(f"Connecting to database at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # 1. Identify vouchers to delete
        cursor.execute("SELECT voucher_number FROM vouchers WHERE voucher_type IN ('Reversal', 'REV')")
        vouchers_to_delete = [row[0] for row in cursor.fetchall()]
        
        if not vouchers_to_delete:
            print("No 'Reversal' or 'REV' vouchers found.")
            return

        print(f"Found {len(vouchers_to_delete)} vouchers to delete: {vouchers_to_delete}")
        
        placeholders = ','.join('?' for _ in vouchers_to_delete)
        
        # 2. Delete related entries
        # Ledger Entries
        cursor.execute(f"DELETE FROM ledger_entries WHERE voucher_number IN ({placeholders})", vouchers_to_delete)
        print(f"Deleted related ledger_entries.")
        
        # Item Entries
        cursor.execute(f"DELETE FROM item_entries WHERE voucher_number IN ({placeholders})", vouchers_to_delete)
        print(f"Deleted related item_entries.")
        
        # Additional Charge Entries
        cursor.execute(f"DELETE FROM additional_charge_entries WHERE voucher_number IN ({placeholders})", vouchers_to_delete)
        print(f"Deleted related additional_charge_entries.")
        
        # 3. Delete the vouchers
        cursor.execute(f"DELETE FROM vouchers WHERE voucher_number IN ({placeholders})", vouchers_to_delete)
        print(f"Deleted vouchers.")
        
        conn.commit()
        print("Cleanup completed successfully.")
        
    except Exception as e:
        conn.rollback()
        print(f"Error during cleanup: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    cleanup_rev_vouchers()
