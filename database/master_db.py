# import sqlite3 - removed
import os
from werkzeug.security import generate_password_hash
from .config import get_connection, execute_insert_returning_id

# ...

def get_all_users():
    conn = get_connection()
    # conn.row_factory = sqlite3.Row - Removed
    users = conn.execute("SELECT * FROM users").fetchall()
    conn.close()
    return [dict(u) for u in users]

def get_user_by_id(user_id):
    conn = get_connection()
    # conn.row_factory = sqlite3.Row - Removed
    user = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
    conn.close()
    return dict(user) if user else None

def get_user_by_username(username):
    conn = get_connection()
    # conn.row_factory = sqlite3.Row - Removed
    user = conn.execute("SELECT * FROM users WHERE username = %s", (username,)).fetchone()
    conn.close()
    return dict(user) if user else None

def get_user_by_login_id(login_id):
    """Get user by username or email"""
    conn = get_connection()
    # conn.row_factory = sqlite3.Row - Removed
    user = conn.execute(
        "SELECT * FROM users WHERE username = %s OR email = %s", 
        (login_id, login_id)
    ).fetchone()
    conn.close()
    return dict(user) if user else None

def add_user(username, email, password_hash, is_admin=0, is_principal=0):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO users (username, email, password_hash, is_admin, is_principal) VALUES (%s, %s, %s, %s, %s)",
            (username, email, password_hash, is_admin, is_principal)
        )
        conn.commit()
    finally:
        conn.close()


# --- User Permissions (menu access) ---

def get_user_permissions(user_id):
    """Return the set of permission keys assigned to a user."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT perm_key FROM user_permissions WHERE user_id = %s", (user_id,)
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def set_user_permissions(user_id, perm_keys):
    """Replace a user's permission set with the given keys."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM user_permissions WHERE user_id = %s", (user_id,))
        for key in perm_keys:
            conn.execute(
                "INSERT INTO user_permissions (user_id, perm_key) VALUES (%s, %s)",
                (user_id, key)
            )
        conn.commit()
    finally:
        conn.close()


def set_user_principal(user_id, is_principal):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE users SET is_principal = %s WHERE id = %s",
            (1 if is_principal else 0, user_id)
        )
        conn.commit()
    finally:
        conn.close()

def set_user_hide_dashboard(user_id, hide_dashboard):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE users SET hide_dashboard = %s WHERE id = %s",
            (1 if hide_dashboard else 0, user_id)
        )
        conn.commit()
    finally:
        conn.close()

def update_user(user_id, email, password_hash=None):
    conn = get_connection()
    try:
        if password_hash:
            conn.execute(
                "UPDATE users SET email = %s, password_hash = %s WHERE id = %s",
                (email, password_hash, user_id)
            )
        else:
            conn.execute(
                "UPDATE users SET email = %s WHERE id = %s",
                (email, user_id)
            )
        conn.commit()
    finally:
        conn.close()

def delete_user(user_id):
    conn = get_connection()
    try:
        # Clean up dependent rows so nothing is left orphaned
        conn.execute("DELETE FROM user_permissions WHERE user_id = %s", (user_id,))
        conn.execute("DELETE FROM user_company_access WHERE user_id = %s", (user_id,))
        conn.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
    finally:
        conn.close()

# --- Company Management ---

def create_company(name):
    """Create a new company in the unified DB"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        company_id = execute_insert_returning_id(
            cursor,
            "INSERT INTO companies (name) VALUES (%s)",
            (name,)
        )
        conn.commit()
        return company_id
    finally:
        conn.close()

def register_company(name, db_path=None):
    """
    Legacy wrapper for create_company. 
    db_path is ignored as we use unified DB.
    """
    return create_company(name)

def get_all_companies():
    conn = get_connection()
    # conn.row_factory = sqlite3.Row - Removed
    companies = conn.execute("SELECT * FROM companies").fetchall()
    conn.close()
    return [dict(c) for c in companies]

def get_company_by_id(company_id):
    conn = get_connection()
    # conn.row_factory = sqlite3.Row - Removed
    company = conn.execute("SELECT * FROM companies WHERE id = %s", (company_id,)).fetchone()
    conn.close()
    return dict(company) if company else None

# --- Assignment Management ---

def assign_company_to_user(user_id, company_id, role='User'):
    conn = get_connection()
    try:
        # PostgreSQL uses ON CONFLICT DO NOTHING instead of INSERT OR IGNORE
        conn.execute(
            "INSERT INTO user_company_access (user_id, company_id, role) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (user_id, company_id, role)
        )
        conn.commit()
    finally:
        conn.close()

def remove_company_from_user(user_id, company_id):
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM user_company_access WHERE user_id = %s AND company_id = %s",
            (user_id, company_id)
        )
        conn.commit()
    finally:
        conn.close()

def get_user_companies(user_id):
    """Get companies accessible by the user"""
    conn = get_connection()
    # conn.row_factory = sqlite3.Row - Removed
    
    # Check if admin
    user = conn.execute("SELECT is_admin FROM users WHERE id = %s", (user_id,)).fetchone()
    if user and user['is_admin']:
        # Admin accesses ALL companies
        companies = conn.execute("SELECT * FROM companies").fetchall()
    else:
        # Regular user accesses assigned companies
        companies = conn.execute("""
            SELECT c.*, uca.role 
            FROM companies c
            JOIN user_company_access uca ON c.id = uca.company_id
            WHERE uca.user_id = %s
        """, (user_id,)).fetchall()
        
    conn.close()
    return [dict(c) for c in companies]

# --- System Settings ---

def set_system_setting(key, value):
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # Postgres ON CONFLICT
        conn.execute(
            "INSERT INTO system_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (key, value)
        )
        conn.commit()
    finally:
        conn.close()

def get_system_setting(key):
    conn = get_connection()
    # conn.row_factory = sqlite3.Row - Removed
    try:
        # Check table exists first to avoid error
        # Postgres way to check table existence
        cursor = conn.execute("SELECT to_regclass('public.system_settings')")
        if not cursor.fetchone()[0]:
             return None
            
        row = conn.execute("SELECT value FROM system_settings WHERE key = %s", (key,)).fetchone()
        return row['value'] if row else None
    except Exception:
        return None
    finally:
        conn.close()
