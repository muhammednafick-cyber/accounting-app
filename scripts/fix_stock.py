
import sys
import os
import sqlite3
import time

# Ensure proper path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import config
config.DB_PATH = 'd:/Accounting App with Import/web-accounting-app_with import v17 anti/Talab_Mart.db'

from database.config import get_connection
from database.vouchers_db import add_voucher

def fix_inventory_opening_balances():
    print("Starting Inventory Opening Balance Fix (Target: Talab_Mart.db)...", flush=True)
    
    # 1. Setup Connection
    try:
        conn = get_connection()
        conn.execute("PRAGMA foreign_keys = ON")
        cursor = conn.cursor()
    except Exception as e:
        print(f"Failed to connect to DB: {e}", flush=True)
        return

    try:
        # 2. Ensure Financial Year 2024
        print("Checking Financial Year...", flush=True)
        cursor.execute("SELECT COUNT(*) FROM financial_years WHERE start_date <= '2024-01-01' AND end_date >= '2024-01-01'")
        if cursor.fetchone()[0] == 0:
            print("Creating FY-2024...", flush=True)
            cursor.execute(
                "INSERT INTO financial_years (fy_code, start_date, end_date, is_active, is_locked) VALUES (?, ?, ?, ?, ?)",
                ("FY-2024", "2024-01-01", "2024-12-31", 1, 0)
            )
            conn.commit()
            print("FY-2024 Created.", flush=True)
        else:
            print("FY-2024 Exists.", flush=True)

        # 3. Ensure Inventory Ledger
        print("Checking Inventory Ledger...", flush=True)
        cursor.execute("SELECT COUNT(*) FROM ledgers WHERE ledger_name = 'Inventory'")
        if cursor.fetchone()[0] == 0:
             print("Creating 'Inventory' ledger...", flush=True)
             cursor.execute(
                "INSERT INTO ledgers (ledger_code, ledger_name, group_code, opening_balance, opening_balance_type, closing_balance) VALUES (?, ?, ?, ?, ?, ?)",
                ("LINV", "Inventory", "G010", 0, "Debit", 0)
             )
             conn.commit()

        # 4. Get Items
        print("Fetching Items...", flush=True)
        cursor.execute("SELECT name, stock_quantity, opening_price, unit_price, opening_location_name, stock_value FROM inventory WHERE stock_quantity > 0")
        items = cursor.fetchall()
        print(f"Found {len(items)} items with quantity > 0.", flush=True)
        
        fixed_count = 0
        skipped_count = 0
        
        for idx, (item_name, current_qty, opening_price, unit_price, loc_name, current_val) in enumerate(items):
             # Progress Monitor
            if idx % 50 == 0:
                print(f"Processing item {idx}/{len(items)}...", flush=True)

            # Check if Opening Voucher Exists
            cursor.execute("SELECT COUNT(*) FROM item_entries ie JOIN vouchers v ON ie.voucher_number = v.voucher_number WHERE ie.item_name = ? AND v.voucher_type = 'Opening'", (item_name,))
            has_opening = cursor.fetchone()[0] > 0
            
            if not has_opening:
                price = opening_price if (opening_price and opening_price > 0) else unit_price
                if not price: price = 0.0
                
                amount = round(current_qty * price, 2)
                
                # Check if Item Name Exists (FK Safety)
                cursor.execute("SELECT name FROM inventory WHERE name=?", (item_name,))
                if not cursor.fetchone():
                     print(f"Skipping {item_name}: Does not exist in inventory table (Data Mismatch?)", flush=True)
                     continue

                # Prepare Voucher Data
                item_entries = [{
                    'item_name': item_name,
                    'quantity': current_qty,
                    'unit_price': price,
                    'amount': amount,
                    'ledger_name': 'Inventory',
                    'type': 'Debit',
                    '_location_override': loc_name or 'Main Location'
                }]
                
                try:
                    add_voucher(
                        voucher_type='Opening',
                        date='2024-01-01',
                        ledger_entries=[],
                        item_entries=item_entries,
                        narration=f"System Fix Opening Stock for {item_name}",
                        location_name=loc_name or 'Main Location',
                        skip_recalc=False,
                        # Pass connection? add_voucher creates its own.
                    )
                    
                    # Also Update Master Value if 0
                    if not current_val or current_val == 0:
                        cursor.execute("UPDATE inventory SET stock_value = ? WHERE name=?", (amount, item_name))
                        conn.commit()

                    fixed_count += 1
                except Exception as e:
                    print(f"Error creating voucher for {item_name}: {e}", flush=True)
            else:
                skipped_count += 1

        print(f"Fix Complete. Fixed: {fixed_count}, Skipped: {skipped_count}", flush=True)
        
    except Exception as e:
        print(f"Fatal Error: {e}", flush=True)
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

if __name__ == "__main__":
    fix_inventory_opening_balances()
