
import sys
import os

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from database.vouchers_db import add_voucher
from database.config import get_connection

def test_add_voucher():
    print("Testing add_voucher...")
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get a company ID
    cursor.execute("SELECT id FROM companies LIMIT 1")
    company_row = cursor.fetchone()
    if not company_row:
        # Create a company if none exists
        cursor.execute("INSERT INTO companies (name) VALUES ('Test Company') RETURNING id")
        company_id = cursor.fetchone()['id']
        conn.commit()
    else:
        company_id = company_row['id']
    
    print(f"Using Company ID: {company_id}")
    
    # Create Financial Year
    cursor.execute("SELECT * FROM financial_years WHERE company_id = %s AND fy_code = 'FY2025'", (company_id,))
    if not cursor.fetchone():
        cursor.execute("""
            INSERT INTO financial_years (company_id, fy_code, start_date, end_date, is_locked)
            VALUES (%s, 'FY2025', '2025-01-01', '2025-12-31', 0)
        """, (company_id,))
        conn.commit()
        print("Created FY2025")
    
    # Create Unit and Group
    cursor.execute("INSERT INTO units (company_id, unit_code, unit_name) VALUES (%s, 'PCS', 'Pieces') ON CONFLICT DO NOTHING", (company_id,))
    cursor.execute("INSERT INTO inventory_groups (company_id, group_code, group_name) VALUES (%s, 'DEFAULT', 'Default Group') ON CONFLICT DO NOTHING", (company_id,))
    conn.commit()

    # Create Inventory Item
    cursor.execute("SELECT * FROM inventory WHERE company_id = %s AND name = 'Test Item'", (company_id,))
    if not cursor.fetchone():
        cursor.execute("""
            INSERT INTO inventory (company_id, name, item_code, stock_group_code, unit_code, stock_quantity, unit_price, vat_rate)
            VALUES (%s, 'Test Item', 'TEST001', 'DEFAULT', 'PCS', 0, 150, 0)
        """, (company_id,))
        conn.commit()
        print("Created Test Item")

    # Check if Inventory ledger exists
    cursor.execute("SELECT * FROM ledgers WHERE ledger_name = 'Inventory' AND company_id = %s", (company_id,))
    inv = cursor.fetchone()
    print(f"Inventory ledger before: {inv}")
    
    # Try adding an opening voucher
    try:
        item_entries = [{
            'item_name': 'Test Item',
            'quantity': 10,
            'unit_price': 100,
            'amount': 1000,
            'ledger_name': 'Inventory',
            'type': 'Debit'
        }]
        
        print("Calling add_voucher...")
        add_voucher(
            voucher_type='Opening',
            date='2025-01-01',
            ledger_entries=[],
            item_entries=item_entries,
            narration='Test Opening Voucher',
            location_name='Main Location',
            company_id=company_id
        )
        print("add_voucher called successfully.")
        
    except Exception as e:
        print(f"add_voucher failed: {e}")
        import traceback
        traceback.print_exc()

    # Check if Inventory ledger exists after
    cursor.execute("SELECT * FROM ledgers WHERE ledger_name = 'Inventory' AND company_id = %s", (company_id,))
    inv_after = cursor.fetchone()
    print(f"Inventory ledger after: {inv_after}")
    
    # Check if voucher was created
    cursor.execute("SELECT * FROM vouchers WHERE voucher_type = 'Opening' AND company_id = %s ORDER BY id DESC LIMIT 1", (company_id,))
    v = cursor.fetchone()
    print(f"Voucher created: {v}")
    
    conn.close()

if __name__ == "__main__":
    test_add_voucher()
