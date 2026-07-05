"""
User management module - Redirects to Master DB
"""
from .master_db import (
    add_user as master_add_user,
    get_user_by_username as master_get_user_by_username,
    get_user_by_login_id as master_get_user_by_login_id,
    get_user_by_id as master_get_user_by_id,
    get_all_users as master_get_all_users,
    update_user as master_update_user,
    delete_user as master_delete_user
)
from .unified_db import init_unified_db

def _initialize_users_table():
    """Initialize users table in Master DB (now Unified DB)"""
    init_unified_db()
    print("Initialized unified database")

def add_user(username, email, password_hash, is_admin=0, is_principal=0):
    return master_add_user(username, email, password_hash, is_admin, is_principal)

def _tuple(user):
    # id, username, email, password_hash, is_admin, is_principal
    return (user['id'], user['username'], user['email'], user['password_hash'],
            user['is_admin'], user.get('is_principal', 0) or 0)

def get_user_by_username(username):
    user = master_get_user_by_username(username)
    return _tuple(user) if user else None

def get_user_by_login_id(login_id):
    """Get user by username or email"""
    user = master_get_user_by_login_id(login_id)
    return _tuple(user) if user else None

def get_user_by_id(user_id):
    user = master_get_user_by_id(user_id)
    return _tuple(user) if user else None

def get_all_users():
    users = master_get_all_users()
    # The caller expects list of tuples/rows: id, username, email, is_admin, is_principal
    result = []
    for user in users:
        result.append((user['id'], user['username'], user['email'],
                       user['is_admin'], user.get('is_principal', 0) or 0))
    return result

def update_user(user_id, email, password_hash=None):
    return master_update_user(user_id, email, password_hash)

def delete_user(user_id):
    return master_delete_user(user_id)
