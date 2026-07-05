import sys
import os
import psycopg2
import sqlite3

# Ensure proper path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.config import get_connection, DB_TYPE, DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT
from database.unified_db import init_unified_db

def reset_db():
    print(f"Resetting database ({DB_TYPE})...")
    conn = get_connection()
    
    if DB_TYPE == "postgres":
        # PostgreSQL: Drop strings for tables
        try:
             # We use the raw connection directly to ensure we can access needed properties if wrapper hides them
             # But the wrapper logic in config.py is simple.
             # However, get_connection returns a wrapper.
             # Let's verify if we can just execute SQL.
             
             # Fetch all tables
             cursor = conn.cursor()
             cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
             tables = cursor.fetchall()
             
             if not tables:
                 print("No tables found to drop.")
             else:
                 print(f"Found {len(tables)} tables. Dropping...")
                 # Disable foreign key checks isn't straightforward in PG globally for a session without superuser sometimes, 
                 # but CASCADE handles it in DROP.
                 
                 for table_row in tables:
                     # table_row might be a dict (RealDictCursor) or tuple depending on config
                     # config.py uses DictCursor for PG.
                     if isinstance(table_row, dict) or hasattr(table_row, 'keys'):
                        table_name = table_row['tablename']
                     else:
                        table_name = table_row[0]
                        
                     print(f"Dropping {table_name}...")
                     # Use CASCADE to remove dependent tables/constraints
                     cursor.execute(f"DROP TABLE IF EXISTS \"{table_name}\" CASCADE")
                 
                 conn.commit()
                 print("All tables dropped.")
                 
        except Exception as e:
            print(f"Error dropping PostgreSQL tables: {e}")
            conn.rollback()
            return
            
    else:
        # SQLite
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()
            
            # SQLite doesn't support CASCADE in DROP TABLE, but we can turn off foreign keys
            cursor.execute("PRAGMA foreign_keys = OFF")
            
            for table_row in tables:
                 if isinstance(table_row, dict): # If dict cursor wrapper used
                     table_name = table_row['name']
                 else:
                     table_name = table_row[0]
                     
                 if table_name == "sqlite_sequence":
                     continue
                     
                 print(f"Dropping {table_name}...")
                 cursor.execute(f"DROP TABLE IF EXISTS \"{table_name}\"")
            
            conn.commit()
            print("All tables dropped.")
            
        except Exception as e:
            print(f"Error dropping SQLite tables: {e}")
            return
            
    conn.close()
    
    print("Re-initializing database schema...")
    init_unified_db()
    print("Database reset complete. Default admin created.")

if __name__ == "__main__":
    reset_db()
