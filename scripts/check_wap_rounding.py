
import sqlite3
from database.config import get_connection

def check_item():
    conn = get_connection()
    cursor = conn.cursor()
    item_name = "6OZ PAPER CUP - TEA4545"
    
    print(f"Checking Item: {item_name}")
    
    # Check Item Entries Last State
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
        qty, val = row
        print(f"DB Running Qty: {qty}")
        print(f"DB Running Val: {val}")
        if qty != 0:
            wap = val / qty
            print(f"Calculated WA: {wap}")
            print(f"Rounded WAP (2 places): {round(wap, 2)}")
            print(f"Rounded WAP (4 places): {round(wap, 4)}")
            print(f"Qty * Rounded WAP (2): {qty * round(wap, 2)}")
            print(f"Diff: {(qty * round(wap, 2)) - val}")
    else:
        print("No item entries found.")

    # Check Inventory Table
    cursor.execute("SELECT stock_quantity, stock_value FROM inventory WHERE company_id = 1 AND name = ?", (item_name,))
    inv_row = cursor.fetchone()
    if inv_row:
        print(f"Inventory Table Qty: {inv_row[0]}")
        print(f"Inventory Table Val: {inv_row[1]}")

    conn.close()

if __name__ == "__main__":
    check_item()
