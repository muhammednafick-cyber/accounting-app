import os
import psycopg2
from psycopg2.extras import DictCursor
from dotenv import load_dotenv

load_dotenv()

# Database Configuration — PostgreSQL only
DB_TYPE = "postgres"  # kept for callers that branch on engine type
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "accounting_unified")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# Single-variable configuration (e.g. Render's DATABASE_URL). Overrides the
# individual DB_* settings above when present.
_database_url = os.getenv("DATABASE_URL")
print(f"[DB CONFIG] DATABASE_URL set: {bool(_database_url)} | DB_HOST: {DB_HOST}")
if _database_url:
    from urllib.parse import urlparse
    _parsed = urlparse(_database_url)
    DB_HOST = _parsed.hostname or DB_HOST
    DB_PORT = str(_parsed.port or 5432)
    DB_NAME = (_parsed.path or "/").lstrip("/") or DB_NAME
    DB_USER = _parsed.username or DB_USER
    DB_PASSWORD = _parsed.password or DB_PASSWORD


class PGCursorWrapper:
    """
    Wrapper for psycopg2 cursor that accepts legacy '?' placeholders
    (converted to '%s') so older queries keep working.
    """
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, query, vars=None):
        if vars:
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
        # PostgreSQL has no cursor.lastrowid; use execute_insert_returning_id.
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
    """Get a PostgreSQL connection."""
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
        raise RuntimeError(
            f"Could not connect to PostgreSQL ({DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}): {e}. "
            "Check your .env / DATABASE_URL settings and that the database server is running."
        ) from e


def execute_insert_returning_id(cursor, sql, params):
    """
    Executes an INSERT statement and returns the new ID (PostgreSQL RETURNING id).

    Args:
        cursor: The database cursor.
        sql: The INSERT SQL statement (WITHOUT 'RETURNING id').
        params: The parameters for the SQL statement.

    Returns:
        The ID of the inserted row.
    """
    sql += " RETURNING id"
    cursor.execute(sql, params)
    row = cursor.fetchone()
    if row is None:
        raise Exception("Insert did not return id")
    if isinstance(row, dict) or hasattr(row, 'keys'):
        return row['id']
    return row[0]
