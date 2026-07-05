
import sqlite3
from database.config import get_connection

def inspect_schema():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(inventory)")
    rows = cursor.fetchall()
    print("--- Inventory Table Schema ---")
    for r in rows:
        print(r)
    conn.close()

if __name__ == "__main__":
    inspect_schema()
