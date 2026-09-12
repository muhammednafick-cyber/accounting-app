"""What an agent asked the books, and when.

An agent reading a client's accounts deserves the same traceability a person
gets. "Which agent read the payroll ledger, and when" is a question that will
eventually be asked, and without this there is no answer.

Deliberately narrow: the tool's name, who ran it, against which company, how
long it took and whether it worked. Not the arguments - a question can carry a
customer name or an amount, and a log of those is a second copy of the data
with weaker protection than the ledger itself.
"""
from .config import get_connection

# Long enough to investigate a complaint, short enough not to become a record
# system of its own.
MCP_LOG_TTL_DAYS = 90


def init_mcp_log_table():
    """Create the table. Safe to call repeatedly."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mcp_call_log (
                id BIGSERIAL PRIMARY KEY,
                user_id INTEGER,
                company_id INTEGER,
                tool_name TEXT NOT NULL,
                outcome TEXT NOT NULL,
                duration_ms INTEGER,
                called_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_mcp_call_log_lookup
            ON mcp_call_log (company_id, called_at DESC)
        """)
        conn.commit()
    finally:
        conn.close()


def record_mcp_call(user_id, company_id, tool_name, outcome, duration_ms):
    """Note one tool call. Trims itself as it goes."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO mcp_call_log
                (user_id, company_id, tool_name, outcome, duration_ms)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, company_id, tool_name, outcome, duration_ms))
        cursor.execute("""
            DELETE FROM mcp_call_log
            WHERE called_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')
        """, (MCP_LOG_TTL_DAYS,))
        conn.commit()
    finally:
        conn.close()


def recent_mcp_calls(company_id, limit=100):
    """The latest calls for one company, newest first."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT tool_name, outcome, duration_ms, called_at, user_id
            FROM mcp_call_log
            WHERE company_id = %s
            ORDER BY called_at DESC
            LIMIT %s
        """, (company_id, limit))
        return [{"tool": r[0], "outcome": r[1], "duration_ms": r[2],
                 "called_at": r[3], "user_id": r[4]} for r in cursor.fetchall()]
    finally:
        conn.close()
