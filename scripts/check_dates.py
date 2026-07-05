import sqlite3
import os

DB_PATH = r'd:/Accounting App with Import/web-accounting-app_with import v4/accounting_unified.db'

def check_dates():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    vouchers = ['OPEN-00001', 'PUR-00007', 'SAL-00006']
    print(f"Checking dates for: {vouchers}")
    
    cursor.execute(f"SELECT voucher_number, date, voucher_id FROM vouchers WHERE voucher_number IN ({','.join(['?']*len(vouchers))})", vouchers)
    rows = cursor.fetchall()
    
    print(f"{'Voucher':<15} | {'Date':<15} | {'ID':<10}")
    print("-" * 45)
    for vn, dt, vid in rows:
        print(f"{vn:<15} | {dt:<15} | {vid:<10}")

    conn.close()

if __name__ == "__main__":
    check_dates()
