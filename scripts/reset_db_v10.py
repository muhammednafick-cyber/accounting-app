import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from database.config import get_connection, DB_TYPE
from database.unified_db import init_unified_db

def reset_db():
    print(f"Resetting database ({DB_TYPE})...")
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        if DB_TYPE == "postgres":
            # Postgres: Drop all tables with CASCADE
            cursor.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
            """)
            tables = cursor.fetchall()
            
            for row in tables:
                # Handle DictCursor vs Tuple
                table_name = row['table_name'] if isinstance(row, dict) else row[0]
                print(f"Dropping table {table_name}...")
                cursor.execute(f"DROP TABLE IF EXISTS \"{table_name}\" CASCADE")
                
        else:
            # SQLite: Disable FKs and drop tables
            conn.execute("PRAGMA foreign_keys = OFF")
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = cursor.fetchall()
            
            for row in tables:
                table_name = row['name'] if isinstance(row, dict) else row[0]
                print(f"Dropping table {table_name}...")
                cursor.execute(f"DROP TABLE IF EXISTS \"{table_name}\"")
                
            conn.execute("PRAGMA foreign_keys = ON")
            
        conn.commit()
        print("All tables dropped.")
        
    except Exception as e:
        conn.rollback()
        print(f"Error dropping tables: {e}")
        return
    finally:
        conn.close()

    print("Re-initializing database schema...")
    init_unified_db()
    print("Database reset complete.")

if __name__ == "__main__":
    reset_db()
