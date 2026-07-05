import os
import sys
import sqlite3

# Ensure project root is on sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import database.config as db_config
from database.reports_db import get_trial_balance_data, get_closing_inventory_data

def main():
    db_config.DB_PATH = os.path.abspath('Nafi.db')
    print('Using DB_PATH =', db_config.DB_PATH)

    conn = db_config.get_connection()
    c = conn.cursor()

    try:
        c.execute("SELECT name, stock_quantity, COALESCE(opening_price,0), COALESCE(opening_location_name,'Main Location') FROM inventory")
        rows = c.fetchall()
        print('Inventory items:', rows)
    except Exception as e:
        print('Inventory query error:', e)

    try:
        c.execute("SELECT voucher_number, voucher_type, date, amount FROM vouchers WHERE voucher_type='Opening' ORDER BY date ASC, voucher_id ASC")
        opening_vouchers = c.fetchall()
        print('Opening vouchers:', opening_vouchers)
        for vno, vtype, vdate, amt in opening_vouchers:
            c.execute("SELECT ledger_name, amount, type FROM ledger_entries WHERE voucher_number = ?", (vno,))
            print('  Ledger entries for', vno, ':', c.fetchall())
            c.execute("SELECT item_name, quantity, unit_price, amount, type FROM item_entries WHERE voucher_number = ?", (vno,))
            print('  Item entries for', vno, ':', c.fetchall())
    except Exception as e:
        print('Opening vouchers query error:', e)

    try:
        tb, total_dr, total_cr = get_trial_balance_data()
        print('TB count:', len(tb), 'Total Dr:', round(total_dr, 2), 'Total Cr:', round(total_cr, 2))
        print('TB rows:', tb)
        inv_lines = [x for x in tb if x.get('ledger_name') == 'Inventory']
        print('Inventory TB lines:', inv_lines)
    except Exception as e:
        print('TB error:', e)

    try:
        closing, total = get_closing_inventory_data()
        print('Closing inventory total:', total)
    except Exception as e:
        print('Closing inventory error:', e)

    conn.close()

if __name__ == '__main__':
    main()
