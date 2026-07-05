import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.config import get_connection, DB_TYPE, DB_NAME, DB_HOST

def check_ledgers():
    print(f"Checking DB: Type={DB_TYPE}, Name={DB_NAME}, Host={DB_HOST}")
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Check companies
        try:
            cursor.execute('SELECT * FROM companies')
            companies = cursor.fetchall()
            print(f"Total companies: {len(companies)}")
            for c in companies:
                print(f"Company: {c}")
        except Exception as e:
             print(f"Error reading companies: {e}")

        # Check ledgers
        try:
            cursor.execute('SELECT count(*) FROM ledgers')
            count = cursor.fetchone()
            print(f"Total ledgers count: {count}")
            
            cursor.execute('SELECT * FROM ledgers LIMIT 5')
            ledgers = cursor.fetchall()
            for l in ledgers:
                print(f"Ledger sample: {l}")
            
            # Check specific lazy ledgers
            print("Checking specific lazy ledgers:")
            cursor.execute("SELECT ledger_name, ledger_code FROM ledgers WHERE ledger_code IN ('LINV', 'LCOGS', 'EXP001') OR ledger_name IN ('Inventory', 'Cost of Goods Sold', 'Depreciation Expense')")
            found = cursor.fetchall()
            print(f"Found lazy ledgers: {found}")

        except Exception as e:
            print(f"Error reading ledgers: {e}")
            
        conn.close()
    except Exception as e:
        print(f"Error connecting: {e}")

if __name__ == "__main__":
    check_ledgers()
