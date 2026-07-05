
import sys
import os
import time

# Add parent directory to path to import modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from database.config import get_connection
from database.unified_db import init_unified_db
from database.users_db import get_user_by_username

def test_connection():
    print("Testing database connection...")
    conn = get_connection()
    if conn:
        print("[OK] Connection successful!")
        conn.close()
    else:
        print("[FAIL] Connection failed!")
        return False
    return True

def test_initialization():
    print("Testing database initialization (schema creation)...")
    try:
        init_unified_db()
        print("[OK] Database initialized successfully!")
    except Exception as e:
        print(f"[FAIL] Database initialization failed: {e}")
        return False
    return True

def test_data_retrieval():
    print("Testing data retrieval (checking for 'admin' user)...")
    try:
        user = get_user_by_username('admin')
        if user:
            # users_db.get_user_by_username returns a tuple: (id, username, email, password_hash, is_admin)
            print(f"[OK] Found user 'admin': {user[1]}")
        else:
            print("[WARN] User 'admin' not found (this might be expected if db is empty and init didn't create it, but ensure_admin_user should have)")
    except Exception as e:
        print(f"[FAIL] Data retrieval failed: {e}")
        return False
    return True

if __name__ == "__main__":
    if test_connection():
        if test_initialization():
            test_data_retrieval()
