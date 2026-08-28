"""Shared runtime state for the app, kept in PostgreSQL rather than memory.

Four things used to live in process memory: the chat assistant's conversation
context, the tokens for exporting a chat answer, the AI rate-limit counters,
and the background job registry. That is correct for exactly one Gunicorn
worker and wrong for any other number, because separate workers do not share
memory - a follow-up question would land on a process that never saw the first
one, and per-process rate limits would multiply by the worker count.

Keeping it here means the app can run `--workers N`, and it also fixes a
single-worker problem: `--max-requests` recycles the worker periodically, which
used to wipe conversations and lose in-flight jobs.

Every table is self-trimming. None of this is business data - it can all be
deleted at any time and the worst outcome is that a user re-phrases a question
or re-uploads a document.
"""
import json

from .config import get_connection


# How long each kind of state stays useful. These are generous: the point is to
# stop unbounded growth, not to expire things a user is still looking at.
CHAT_SESSION_TTL_HOURS = 12
CHAT_EXPORT_TTL_HOURS = 6
RATE_LIMIT_TTL_HOURS = 2
JOB_TTL_HOURS = 24


def init_app_state_tables():
    """Create the shared-state tables. Safe to call repeatedly."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                session_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated
            ON chat_sessions (updated_at)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_exports (
                token TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_exports_created
            ON chat_exports (created_at)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rate_limit_hits (
                id BIGSERIAL PRIMARY KEY,
                bucket TEXT NOT NULL,
                identity TEXT NOT NULL,
                hit_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_rate_limit_lookup
            ON rate_limit_hits (bucket, identity, hit_at)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS background_jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                description TEXT,
                progress TEXT,
                result_json TEXT,
                error TEXT,
                started TEXT,
                finished TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_background_jobs_created
            ON background_jobs (created_at)
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- chat context

def chat_session_load(session_id):
    """The stored conversation state, or None."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT state_json FROM chat_sessions WHERE session_id = %s",
                       (session_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return json.loads(row[0])
    finally:
        conn.close()


def chat_session_save(session_id, state):
    """Write the conversation state back, replacing whatever was there."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO chat_sessions (session_id, state_json, updated_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (session_id) DO UPDATE
              SET state_json = EXCLUDED.state_json,
                  updated_at = CURRENT_TIMESTAMP
        """, (session_id, json.dumps(state, default=str)))
        cursor.execute("""
            DELETE FROM chat_sessions
            WHERE updated_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 hour')
        """, (CHAT_SESSION_TTL_HOURS,))
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------- chat export

def chat_export_save(token, payload):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO chat_exports (token, payload_json)
            VALUES (%s, %s)
            ON CONFLICT (token) DO UPDATE SET payload_json = EXCLUDED.payload_json
        """, (token, json.dumps(payload, default=str)))
        cursor.execute("""
            DELETE FROM chat_exports
            WHERE created_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 hour')
        """, (CHAT_EXPORT_TTL_HOURS,))
        conn.commit()
    finally:
        conn.close()


def chat_export_load(token):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT payload_json FROM chat_exports WHERE token = %s",
                       (token,))
        row = cursor.fetchone()
        return json.loads(row[0]) if row else None
    finally:
        conn.close()


# ------------------------------------------------------------------ rate limit

def rate_limit_check(bucket, identity, limit, window_seconds):
    """Record a hit if under the limit. Returns (allowed, retry_after_seconds).

    The count and the insert run in one transaction so two workers cannot both
    see the last free slot. A rate limiter is allowed to be approximate, but
    not by a factor of the worker count - which is exactly what the in-memory
    version would have been.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*), MIN(hit_at)
            FROM rate_limit_hits
            WHERE bucket = %s AND identity = %s
              AND hit_at > CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
        """, (bucket, identity, window_seconds))
        used, oldest = cursor.fetchone()

        if (used or 0) >= limit:
            retry_after = window_seconds
            if oldest is not None:
                cursor.execute("""
                    SELECT CEIL(EXTRACT(EPOCH FROM
                        (%s::timestamp + (%s * INTERVAL '1 second') - CURRENT_TIMESTAMP)))
                """, (oldest, window_seconds))
                remaining = cursor.fetchone()[0]
                retry_after = int(remaining or 0)
            conn.commit()
            return False, max(1, retry_after)

        cursor.execute(
            "INSERT INTO rate_limit_hits (bucket, identity) VALUES (%s, %s)",
            (bucket, identity))
        cursor.execute("""
            DELETE FROM rate_limit_hits
            WHERE hit_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 hour')
        """, (RATE_LIMIT_TTL_HOURS,))
        conn.commit()
        return True, 0
    finally:
        conn.close()


def rate_limit_clear():
    """Drop every counter. For tests."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM rate_limit_hits")
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------------ jobs

_JOB_COLUMNS = ("job_id", "status", "description", "progress",
                "result_json", "error", "started", "finished")


def _job_row_to_dict(row):
    if not row:
        return None
    job = dict(zip(_JOB_COLUMNS, row))
    raw = job.pop("result_json")
    job["id"] = job.pop("job_id")
    job["result"] = json.loads(raw) if raw else None
    return job


def job_create(job_id, description, started):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO background_jobs
                (job_id, status, description, progress, started)
            VALUES (%s, 'queued', %s, 'Waiting to start...', %s)
        """, (job_id, description, started))
        cursor.execute("""
            DELETE FROM background_jobs
            WHERE created_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 hour')
        """, (JOB_TTL_HOURS,))
        conn.commit()
    finally:
        conn.close()


def job_get(job_id):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT {", ".join(_JOB_COLUMNS)}
            FROM background_jobs WHERE job_id = %s
        """, (job_id,))
        return _job_row_to_dict(cursor.fetchone())
    finally:
        conn.close()


def job_update(job_id, **fields):
    """Patch whichever job columns were passed. `result` is stored as JSON."""
    if "result" in fields:
        fields["result_json"] = json.dumps(fields.pop("result"), default=str)
    allowed = [k for k in fields if k in _JOB_COLUMNS and k != "job_id"]
    if not allowed:
        return
    assignments = ", ".join(f"{name} = %s" for name in allowed)
    params = [fields[name] for name in allowed] + [job_id]
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE background_jobs SET {assignments} WHERE job_id = %s", params)
        conn.commit()
    finally:
        conn.close()
