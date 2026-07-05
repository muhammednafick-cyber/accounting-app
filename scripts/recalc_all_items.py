
import sqlite3
from database.vouchers_db import recalculate_running_balance_for_item
from database.config import get_connection

def recalc_all():
    print("Starting Global Inventory Recalculation...")
    conn = get_connection()
    cursor = conn.cursor()
    
    # 1. Get all unique items
    cursor.execute("SELECT DISTINCT item_name FROM item_entries WHERE company_id = 1")
    items = [row[0] for row in cursor.fetchall()]
    
    print(f"Found {len(items)} items to recalculate.")
    
    for i, item_name in enumerate(items):
        try:
            # Pass company_id=1 explicitly
            recalculate_running_balance_for_item(item_name, company_id=1, db_connection=conn)
            if (i + 1) % 10 == 0:
                print(f"Processed {i + 1}/{len(items)} items...")
        except Exception as e:
            print(f"Error recalculating {item_name}: {e}")

    # 2. Update Inventory Table Summary
    print("Recalculation complete. Updating final inventory values...")
    # This logic is seemingly already inside recalculate_running_balance_for_item?
    # Yes, it updates item entries. Does it update 'inventory' table stock_quantity/stock_value?
    # No, recalculate_running_balance_for_item updates item_entries history.
    # We need to ensure the `inventory` table reflects the FINAL state.
    
    # However, vouchers_db.py seems to update 'inventory' table in `add_voucher`.
    # `recalculate_running_balance_for_item` does NOT update `inventory` table directly?
    # Let's check `vouchers_db.py` again. 
    # Actually, `recalculate` logic usually re-derives history but doesn't necessarily set the final inventory table value 
    # UNLESS there's a specific function for that.
    
    # Wait, the 6.00 discrepancy was "Inventory Table" vs "Item History".
    # The fix was in `recalculate_running_balance_for_item`.
    # If `recalculate` only updates `item_entries`, then `inventory` table might still be wrong if it was derived from `add_voucher`.
    # The discrepancy was identified by comparing `inventory` table sum vs `cumulative item history`.
    
    # We should probably reset `inventory` table based on the final running_qty/value of the last item_entry.
    
    for item_name in items:
        cursor.execute("""
            SELECT running_qty, running_value 
            FROM item_entries ie
            JOIN vouchers v ON ie.voucher_number = v.voucher_number
            WHERE ie.company_id = 1 AND ie.item_name = ?
            ORDER BY v.date DESC, v.voucher_id DESC, ie.id DESC
            LIMIT 1
        """, (item_name,))
        row = cursor.fetchone()
        if row:
            final_qty = row[0]
            final_val = row[1]
            cursor.execute("UPDATE inventory SET stock_quantity = ?, stock_value = ? WHERE company_id = 1 AND name = ?", 
                           (final_qty, final_val, item_name))
                           
    conn.commit()
    conn.close()
    print("Global Recalculation & Inventory Sync Complete.")

if __name__ == "__main__":
    recalc_all()
