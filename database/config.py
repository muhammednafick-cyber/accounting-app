import sqlite3
import os
import psycopg2
from psycopg2.extras import DictCursor
from dotenv import load_dotenv

load_dotenv()

# Database Configuration
DB_TYPE = os.getenv("DB_TYPE", "postgres") # Default to postgres if not set, fallback to sqlite if connection fails or configured
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "accounting_unified")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# Single-variable configuration (e.g. Render's DATABASE_URL). Overrides the
# individual DB_* settings above when present.
_database_url = os.getenv("DATABASE_URL")
if _database_url:
    from urllib.parse import urlparse
    _parsed = urlparse(_database_url)
    DB_TYPE = "postgres"
    DB_HOST = _parsed.hostname or DB_HOST
    DB_PORT = str(_parsed.port or 5432)
    DB_NAME = (_parsed.path or "/").lstrip("/") or DB_NAME
    DB_USER = _parsed.username or DB_USER
    DB_PASSWORD = _parsed.password or DB_PASSWORD

# SQLite Fallback Path
SQLITE_DB_PATH = os.path.join(os.path.abspath("."), "accounting_unified.db")
DB_PATH = SQLITE_DB_PATH

class PGCursorWrapper:
    """
    Wrapper for psycopg2 cursor to handle SQLite-style '?' placeholders.
    """
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, query, vars=None):
        if vars:
            # Replace '?' with '%s' for PostgreSQL
            # Note: This is a simple replacement. If '?' appears in strings/comments, it might break.
            # Ideally use a proper sql parser or regex if complexity increases.
            query = query.replace('?', '%s')
            return self.cursor.execute(query, vars)
        return self.cursor.execute(query)

    def executemany(self, query, vars_list):
        if vars_list:
            query = query.replace('?', '%s')
            return self.cursor.executemany(query, vars_list)
        return self.cursor.executemany(query)

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    def fetchmany(self, size=None):
        return self.cursor.fetchmany(size)
    
    @property
    def rowcount(self):
        return self.cursor.rowcount
        
    @property
    def lastrowid(self):
        # PostgreSQL doesn't support lastrowid directly on the cursor like SQLite.
        # Typically one uses RETURNING id in the INSERT statement.
        # This is a limitation of this shim. 
        # For now, we might need to modify INSERTs to use RETURNING id.
        return self.cursor.lastrowid
    
    @property
    def description(self):
        return self.cursor.description

    def close(self):
        self.cursor.close()

    def __iter__(self):
        return self.cursor.__iter__()

    def __getattr__(self, name):
        return getattr(self.cursor, name)


class PGConnectionWrapper:
    """
    Wrapper for psycopg2 connection to provide a factory for PGCursorWrapper.
    """
    def __init__(self, conn):
        self.conn = conn

    def cursor(self):
        return PGCursorWrapper(self.conn.cursor())

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

    def execute(self, query, vars=None):
        # Helper for direct connection execution (shorthand)
        cursor = self.cursor()
        cursor.execute(query, vars)
        return cursor
        
    def __getattr__(self, name):
        return getattr(self.conn, name)

def get_connection():
    """
    Get a connection to the Database.
    Tries PostgreSQL first, falls back to SQLite if configured or connection fails.
    """
    if DB_TYPE == "postgres":
        try:
            conn = psycopg2.connect(
                host=DB_HOST,
                port=DB_PORT,
                dbname=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD,
                cursor_factory=DictCursor
            )
            return PGConnectionWrapper(conn)
        except psycopg2.Error as e:
            print(f"Error connecting to PostgreSQL: {e}")
            print("Falling back to SQLite (check your .env file)")
            pass # Fallthrough to SQLite

    # SQLite Connection
    path = SQLITE_DB_PATH
    conn = sqlite3.connect(path)
    
    # Enable DictCursor-like consistency for SQLite
    conn.row_factory = sqlite3.Row
    
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA cache_size=-64000;") 
        conn.execute("PRAGMA foreign_keys=ON;")
    except sqlite3.Error:
        pass
        
    return SQLiteConnectionWrapper(conn)

class SQLiteCursorWrapper:
    """
    Wrapper for sqlite3 cursor to handle PostgreSQL-style '%s' placeholders
    and ensure compatibility with code expecting a DictCursor-like interface.
    """
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, query, vars=None):
        if vars:
            # Replace '%s' with '?' for SQLite
            # This is a naive replacement. 
            query = query.replace('%s', '?')
            return self.cursor.execute(query, vars)
        return self.cursor.execute(query)

    def executemany(self, query, vars_list):
        if vars_list:
            query = query.replace('%s', '?')
            return self.cursor.executemany(query, vars_list)
        return self.cursor.executemany(query)

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    def fetchmany(self, size=None):
        return self.cursor.fetchmany(size)
    
    @property
    def rowcount(self):
        return self.cursor.rowcount
        
    @property
    def lastrowid(self):
        return self.cursor.lastrowid
    
    @property
    def description(self):
        return self.cursor.description

    def close(self):
        self.cursor.close()

    def __iter__(self):
        return self.cursor.__iter__()

    def __getattr__(self, name):
        return getattr(self.cursor, name)


class SQLiteConnectionWrapper:
    """
    Wrapper for sqlite3 connection to provide a factory for SQLiteCursorWrapper.
    """
    def __init__(self, conn):
        self.conn = conn

    def cursor(self):
        return SQLiteCursorWrapper(self.conn.cursor())

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

    def execute(self, query, vars=None):
        cursor = self.cursor()
        cursor.execute(query, vars)
        return cursor
        
    def __getattr__(self, name):
        return getattr(self.conn, name)

def execute_insert_returning_id(cursor, sql, params):
    """
    Executes an INSERT statement and returns the new ID.
    Handles the difference between PostgreSQL (RETURNING id) and SQLite (lastrowid).
    
    Args:
        cursor: The database cursor.
        sql: The INSERT SQL statement (WITHOUT 'RETURNING id').
        params: The parameters for the SQL statement.
        
    Returns:
        The ID of the inserted row.
    """
    if DB_TYPE == "postgres":
        sql += " RETURNING id"
        cursor.execute(sql, params)
        row = cursor.fetchone()
        if row is None:
            if hasattr(cursor, "lastrowid") and cursor.lastrowid:
                return cursor.lastrowid
            raise Exception("Insert did not return id")
        if isinstance(row, dict) or hasattr(row, 'keys'):
            return row['id']
        if len(row) > 0:
            return row[0]
        if hasattr(cursor, "lastrowid") and cursor.lastrowid:
            return cursor.lastrowid
        raise Exception("Insert did not return id")
    else:
        cursor.execute(sql, params)
        return cursor.lastrowid
