"""A record of imports that have actually been posted.

The queue row is deleted once its rows are in the ledger, which left nothing
to say an import had ever happened. Re-uploading the same spreadsheet - after a
timeout, a lost connection, or simple uncertainty about whether the first
attempt worked - posted every voucher a second time, and the duplicates were
only discovered later in the accounts.

Each completed import is fingerprinted by the content it posted, so an
identical file is recognised and the operator is asked before it goes in again.
The fingerprint is of the parsed rows, not the spreadsheet, so re-exporting the
same data still matches.
"""
import hashlib

from .config import get_connection


def content_fingerprint(json_data):
    """A stable fingerprint for the rows an import would post."""
    if not isinstance(json_data, (str, bytes)):
        return None
    payload = json_data.encode("utf-8") if isinstance(json_data, str) else json_data
    return hashlib.sha256(payload).hexdigest()


def init_import_history_table():
    """Create the table. Safe to call repeatedly."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS completed_imports (
                id BIGSERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                file_name TEXT,
                voucher_type TEXT,
                row_count INTEGER,
                completed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_completed_imports_lookup
            ON completed_imports (company_id, content_hash, completed_at DESC)
        """)
        conn.commit()
    finally:
        conn.close()


def find_completed_import(company_id, content_hash):
    """The most recent posting of this exact content, or None."""
    if not content_hash or not company_id:
        return None
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT file_name, voucher_type, row_count, completed_at
            FROM completed_imports
            WHERE company_id = %s AND content_hash = %s
            ORDER BY completed_at DESC
            LIMIT 1
        """, (company_id, content_hash))
        row = cursor.fetchone()
        if not row:
            return None
        return {"file_name": row[0], "voucher_type": row[1],
                "row_count": row[2], "completed_at": row[3]}
    except Exception:
        # A missing history table must never stop an import being posted.
        return None
    finally:
        conn.close()


def record_completed_import(company_id, content_hash, file_name,
                            voucher_type, row_count, cursor=None):
    """Note that this content has been posted.

    Pass the import's own cursor to record it in the same transaction as the
    vouchers, so the history cannot claim an import that was rolled back.
    """
    if not content_hash or not company_id:
        return
    statement = """
        INSERT INTO completed_imports
            (company_id, content_hash, file_name, voucher_type, row_count)
        VALUES (%s, %s, %s, %s, %s)
    """
    params = (company_id, content_hash, file_name, voucher_type, row_count)
    if cursor is not None:
        cursor.execute(statement, params)
        return
    conn = get_connection()
    try:
        conn.cursor().execute(statement, params)
        conn.commit()
    finally:
        conn.close()
