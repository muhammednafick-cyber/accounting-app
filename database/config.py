import os
import atexit
import threading

import psycopg2
from psycopg2 import pool as pg_pool
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
        """Hand the connection back to the pool instead of dropping it.

        Every caller already does try/finally conn.close(), so this is the
        natural release point. The rollback matters: a connection returned
        mid-transaction would carry that transaction into whoever borrows it
        next.
        """
        release_connection(self.conn)

    def execute(self, query, vars=None):
        # Helper for direct connection execution (shorthand)
        cursor = self.cursor()
        cursor.execute(query, vars)
        return cursor

    def __getattr__(self, name):
        return getattr(self.conn, name)


# ============================================================
# Connection pooling
# ============================================================
#
# Every call used to open a brand new PostgreSQL connection - a TCP connect,
# TLS handshake and authentication for each query batch. On a single small
# instance that is the dominant cost of most requests, and a burst of them can
# exhaust the server's connection limit. A pool reuses a handful instead.

def _pool_size(name, default):
    try:
        return max(1, int(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


DB_POOL_MIN = _pool_size("DB_POOL_MIN", 1)
DB_POOL_MAX = _pool_size("DB_POOL_MAX", 10)

_pool = None
_pool_lock = threading.Lock()


def _connect_kwargs():
    return dict(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER,
                password=DB_PASSWORD, cursor_factory=DictCursor)


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = pg_pool.ThreadedConnectionPool(
                    DB_POOL_MIN, DB_POOL_MAX, **_connect_kwargs())
                print(f"[DB CONFIG] connection pool ready "
                      f"({DB_POOL_MIN}-{DB_POOL_MAX} connections)")
    return _pool


def release_connection(conn):
    """Return a borrowed connection to the pool, or close it if it is unusable."""
    if conn is None:
        return
    pool = _pool
    if pool is None:
        try:
            conn.close()
        except Exception:
            pass
        return
    broken = getattr(conn, "closed", 0)
    if not broken:
        try:
            # Never hand a half-finished transaction to the next borrower.
            conn.rollback()
        except Exception:
            broken = True
    try:
        pool.putconn(conn, close=bool(broken))
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


def close_pool():
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:
                pass
            _pool = None


atexit.register(close_pool)


def get_connection():
    """Get a PostgreSQL connection from the pool."""
    try:
        conn = _get_pool().getconn()
        # A pooled connection can have died since it was last used (idle
        # timeout, server restart). Swap it for a fresh one rather than
        # handing back something that will fail on first use.
        if getattr(conn, "closed", 0):
            try:
                _get_pool().putconn(conn, close=True)
            except Exception:
                pass
            conn = psycopg2.connect(**_connect_kwargs())
        return PGConnectionWrapper(conn)
    except pg_pool.PoolError as e:
        # Pool exhausted: fall back to a direct connection so a burst of
        # traffic degrades in speed rather than failing outright.
        print(f"[DB CONFIG] pool exhausted ({e}) - opening a direct connection")
        try:
            return PGConnectionWrapper(psycopg2.connect(**_connect_kwargs()))
        except psycopg2.Error as exc:
            raise RuntimeError(_connect_error(exc)) from exc
    except psycopg2.Error as e:
        raise RuntimeError(_connect_error(e)) from e


def _connect_error(e):
    return (f"Could not connect to PostgreSQL "
            f"({DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}): {e}. "
            "Check your .env / DATABASE_URL settings and that the database "
            "server is running.")


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
