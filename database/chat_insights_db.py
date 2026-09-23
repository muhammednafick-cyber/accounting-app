"""What the chat could not answer, and what people thought of what it did.

Two small tables. A question the built-in reports cannot match used to be
answered with an apology and forgotten; kept here, the ones asked most often
become the next phrasings to teach the no-AI path - which answers for nothing.
The thumbs are the same idea from the other side: an answer marked wrong is a
report or a phrasing worth a look.

Both are per company, like everything else a client can see.
"""
from .config import get_connection

# One question is not worth storing at any length; a pasted document is not a
# question at all.
MAX_QUESTION = 500


def init_chat_insights_tables():
    """Create the tables. Safe to call repeatedly."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_misses (
                id BIGSERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL,
                user_id INTEGER,
                question TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_misses_company
            ON chat_misses (company_id, created_at DESC)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_feedback (
                id BIGSERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL,
                user_id INTEGER,
                question TEXT NOT NULL,
                tool TEXT,
                vote TEXT NOT NULL CHECK (vote IN ('up', 'down')),
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_feedback_company
            ON chat_feedback (company_id, created_at DESC)
        """)
        conn.commit()
    finally:
        conn.close()


def record_miss(company_id, user_id, question, reason):
    """Note a question nothing could answer. Never raises: a failure to log
    must not become a failure to reply."""
    question = (question or "").strip()[:MAX_QUESTION]
    if not company_id or not question:
        return
    try:
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO chat_misses (company_id, user_id, question, reason) "
                "VALUES (%s, %s, %s, %s)",
                (company_id, user_id, question, (reason or "")[:60]))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        print(f"[chat-insights] could not record a miss: {exc}")


def record_feedback(company_id, user_id, question, tool, vote):
    if vote not in ("up", "down"):
        raise ValueError("vote must be 'up' or 'down'")
    question = (question or "").strip()[:MAX_QUESTION]
    if not company_id or not question:
        raise ValueError("a company and a question are required")
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO chat_feedback (company_id, user_id, question, tool, vote) "
            "VALUES (%s, %s, %s, %s, %s)",
            (company_id, user_id, question, (tool or "")[:80], vote))
        conn.commit()
    finally:
        conn.close()


def top_misses(company_id, days=30, limit=50):
    """The unanswered questions, most often asked first.

    Grouped on the lower-cased, trimmed text so "Sales by city" and "sales by
    city " count as one.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT MIN(question) AS question, COUNT(*) AS asked,
                   MAX(created_at) AS last_asked, MIN(reason) AS reason
            FROM chat_misses
            WHERE company_id = %s
              AND created_at >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')
            GROUP BY LOWER(TRIM(question))
            ORDER BY asked DESC, last_asked DESC
            LIMIT %s
        """, (company_id, days, limit))
        return [{"question": r[0], "asked": r[1], "last_asked": r[2],
                 "reason": r[3]} for r in cursor.fetchall()]
    finally:
        conn.close()


def feedback_summary(company_id, days=30, limit=50):
    """Counts of each vote, and the answers marked wrong, newest first."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT vote, COUNT(*) FROM chat_feedback
            WHERE company_id = %s
              AND created_at >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')
            GROUP BY vote
        """, (company_id, days))
        counts = {r[0]: r[1] for r in cursor.fetchall()}
        cursor.execute("""
            SELECT question, tool, created_at FROM chat_feedback
            WHERE company_id = %s AND vote = 'down'
              AND created_at >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')
            ORDER BY created_at DESC
            LIMIT %s
        """, (company_id, days, limit))
        down = [{"question": r[0], "tool": r[1], "created_at": r[2]}
                for r in cursor.fetchall()]
        return {"up": counts.get("up", 0), "down": counts.get("down", 0),
                "wrong": down}
    finally:
        conn.close()
