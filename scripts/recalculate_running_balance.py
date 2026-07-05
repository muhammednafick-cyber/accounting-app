import sqlite3
import sys
import os
from datetime import datetime

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from database.config import get_connection
from database.vouchers_db import recompute_ledger_closing_balances

def recalculate_running_balance():
    print("Starting Running Balance Recalculation...")
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Ensure Schema
    print("Initializing tables...")
    
    # 2. Reset Inventory
    print("Resetting inventory balances...")
    cursor.execute("UPDATE inventory SET stock_quantity = 0, stock_value = 0")
    
    # 3. State Tracking
    # { item_name: { qty: 0.0, value: 0.0 } }
    inventory_state = {}

    cursor.execute("SELECT name, opening_price FROM inventory WHERE opening_price > 0")
    legacy_opening = cursor.fetchall()
    
    # 4. Fetch all vouchers in chronological order
    cursor.execute("SELECT voucher_number, voucher_type, date FROM vouchers ORDER BY date ASC, voucher_id ASC")
    vouchers = cursor.fetchall()
    print(f"Processing {len(vouchers)} vouchers...")
    
    count = 0
    for v_no, v_type, v_date in vouchers:
        count += 1
        if count % 100 == 0:
            print(f"Processed {count} vouchers...")
            
        cursor.execute("SELECT id, item_name, quantity, unit_price, amount, type, ledger_name, landed_cost_per_unit FROM item_entries WHERE voucher_number = ?", (v_no,))
        entries_raw = cursor.fetchall()
        entries = []
        for r in entries_raw:
            entries.append({
                'id': r[0], 'item_name': r[1], 'quantity': r[2], 'unit_price': r[3], 
                'amount': r[4], 'type': r[5], 'ledger_name': r[6],
                'landed_cost_per_unit': r[7]
            })
            
        # Handle Transfers specifically
        if v_type == 'Inventory Transfer':
            # Simplified logic for brevity in this debug version
             pass

        else:
            # Normal Vouchers
            for entry in entries:
                item = entry['item_name']
                qty = abs(entry['quantity'])
                state = inventory_state.get(item, {'qty': 0.0, 'value': 0.0})
                # WAP logic
                current_wap = state['value'] / state['qty'] if state['qty'] > 0 else 0
                
                if v_type in ['Purchase', 'Opening', 'Sales Return']:
                    # Inward
                    if v_type == 'Sales Return':
                        price = current_wap if state['qty'] > 0 else entry['unit_price']
                    elif v_type == 'Opening':
                        price = entry['unit_price']
                        if state['qty'] == 0: state['value'] = 0
                    else:
                        price = entry['landed_cost_per_unit'] if entry['landed_cost_per_unit'] is not None else entry['unit_price']
                        
                    val = qty * price
                    state['qty'] += qty
                    state['value'] += val
                    inventory_state[item] = state
                    
                    if v_type == 'Sales Return':
                         cursor.execute("UPDATE item_entries SET cogs_rate=?, cogs_amount=?, running_qty=?, running_value=?, running_wap=? WHERE id=?",
                                   (price, val, state['qty'], state['value'], (state['value']/state['qty'] if state['qty'] else 0), entry['id']))
                    else:
                         cursor.execute("UPDATE item_entries SET running_qty=?, running_value=?, running_wap=? WHERE id=?",
                                   (state['qty'], state['value'], (state['value']/state['qty'] if state['qty'] else 0), entry['id']))
                                   
                elif v_type == 'Sales':
                    # Outward
                    cogs_rate = current_wap
                    cogs_amount = qty * cogs_rate
                    
                    cursor.execute("UPDATE item_entries SET cogs_rate=?, cogs_amount=? WHERE id=?", (cogs_rate, cogs_amount, entry['id']))
                    
                    state['qty'] -= qty
                    state['value'] -= cogs_amount
                    inventory_state[item] = state
                    
                    cursor.execute("UPDATE item_entries SET running_qty=?, running_value=?, running_wap=? WHERE id=?",
                                   (state['qty'], state['value'], current_wap, entry['id']))
                                   
                elif v_type == 'Purchase Return':
                    price_out = entry['landed_cost_per_unit'] if entry['landed_cost_per_unit'] is not None else entry['unit_price']
                    val_out = qty * price_out
                    state['qty'] -= qty
                    state['value'] -= val_out
                    inventory_state[item] = state
                    cursor.execute("UPDATE item_entries SET running_qty=?, running_value=?, running_wap=? WHERE id=?",
                                   (state['qty'], state['value'], (state['value']/state['qty'] if state['qty'] else 0), entry['id']))
                
                elif v_type == 'Stock Adjustment':
                    if entry['type'] == 'Debit':
                        price = current_wap if state['qty'] > 0 else entry['unit_price']
                        val = qty * price 
                        cursor.execute("UPDATE item_entries SET unit_price=?, amount=? WHERE id=?", (price, val, entry['id']))
                        state['qty'] += qty
                        state['value'] += val
                    else:
                        val_out = qty * current_wap
                        state['qty'] -= qty
                        state['value'] -= val_out
                        cursor.execute("UPDATE item_entries SET unit_price=?, amount=? WHERE id=?", (current_wap, val_out, entry['id']))
                    inventory_state[item] = state
                    cursor.execute("UPDATE item_entries SET running_qty=?, running_value=?, running_wap=? WHERE id=?",
                                   (state['qty'], state['value'], (state['value']/state['qty'] if state['qty'] else 0), entry['id']))

        # Sync GL Entries
        if v_type in ('Sales', 'Sales Return'):
             cursor.execute("SELECT SUM(cogs_amount) FROM item_entries WHERE voucher_number = ?", (v_no,))
             new_cogs_total = cursor.fetchone()[0] or 0.0
             
             cursor.execute("SELECT amount FROM ledger_entries WHERE voucher_number = ? AND ledger_name = 'Cost of Goods Sold'", (v_no,))
             row_cogs = cursor.fetchone()
             old_cogs_amt = row_cogs[0] if row_cogs else 0.0
             
             if v_no == 'SAL-00006':
                 print(f"DEBUG {v_no}: new_cogs={new_cogs_total}, old_cogs={old_cogs_amt}")

             if abs(new_cogs_total - old_cogs_amt) > 0.001:
                 diff = new_cogs_total - old_cogs_amt
                 
                 if v_no == 'SAL-00006':
                     print(f"DEBUG {v_no}: Updating GL. Diff={diff}")

                 cursor.execute("UPDATE ledger_entries SET amount = ? WHERE voucher_number = ? AND ledger_name IN ('Cost of Goods Sold', 'Inventory')", (new_cogs_total, v_no))
                 
                 if v_type == 'Sales':
                     cursor.execute("UPDATE ledgers SET closing_balance = closing_balance + ? WHERE ledger_name = 'Cost of Goods Sold'", (diff,))
                     cursor.execute("UPDATE ledgers SET closing_balance = closing_balance - ? WHERE ledger_name = 'Inventory'", (diff,))
                 else:
                     cursor.execute("UPDATE ledgers SET closing_balance = closing_balance + ? WHERE ledger_name = 'Inventory'", (diff,))
                     cursor.execute("UPDATE ledgers SET closing_balance = closing_balance - ? WHERE ledger_name = 'Cost of Goods Sold'", (diff,))
                     
        elif v_type == 'Stock Adjustment':
             # Simplified sync
             cursor.execute("SELECT SUM(amount) FROM item_entries WHERE voucher_number = ? AND type = 'Debit'", (v_no,))
             debit_total = cursor.fetchone()[0] or 0.0
             cursor.execute("SELECT SUM(amount) FROM item_entries WHERE voucher_number = ? AND type = 'Credit'", (v_no,))
             credit_total = cursor.fetchone()[0] or 0.0
             
             cursor.execute("SELECT ledger_name, amount, type FROM ledger_entries WHERE voucher_number = ?", (v_no,))
             ledgers = cursor.fetchall()
             
             for lname, lamt, ltype in ledgers:
                 new_amt = lamt
                 # Logic for Inventory/Adjustment ledgers
                 if lname == 'Inventory':
                     new_amt = credit_total if ltype == 'Credit' else debit_total
                 # else logic... simplified
                 
                 if abs(new_amt - lamt) > 0.001:
                     diff = new_amt - lamt
                     cursor.execute("UPDATE ledger_entries SET amount = ? WHERE voucher_number = ? AND ledger_name = ?", (new_amt, v_no, lname))
                     # update ledgers closing balance... simplified

    # 5. Final Inventory Update
    print("Updating final inventory state...")
    for item, state in inventory_state.items():
        cursor.execute("UPDATE inventory SET stock_quantity=?, stock_value=? WHERE name=?", (state['qty'], state['value'], item))
        
    conn.commit()
    conn.close()

    print("Recomputing ledger closing balances...")
    recompute_ledger_closing_balances()
    
    print("Recalculation Complete.")

if __name__ == '__main__':
    if len(sys.argv) > 1:
        database.config.DB_PATH = os.path.abspath(sys.argv[1])
        print(f"Overriding database path to: {database.config.DB_PATH}")
    
    recalculate_running_balance()
