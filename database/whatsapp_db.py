"""WhatsApp and Telegram: which chats may ask the chatbot, and for whom.

One table serves both channels (the `channel` column). The external id is a
WhatsApp phone number, or "tg:<chat id>" for Telegram, so the two can never
collide in the unique wa_id column.


A number is linked to one user and one company. Linking is proved by the phone
itself: the app shows a one-time code, and the link is made only when that
code arrives *from* the number over WhatsApp. So nobody can link a number they
do not hold, and no outbound message (which Meta charges for) is needed.

Rows:
  pending  - wa_id NULL, code_hash set, expires in CODE_TTL_MINUTES
  linked   - wa_id set, code_hash NULL
"""
import hashlib
import json
import secrets

from .config import get_connection

CODE_TTL_MINUTES = 15
SEEN_TTL_DAYS = 7


def init_whatsapp_tables():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS whatsapp_links (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                company_id INTEGER NOT NULL,
                wa_id TEXT UNIQUE,
                code_hash TEXT,
                code_expires TIMESTAMP,
                linked_at TIMESTAMP,
                last_used TIMESTAMP,
                last_token TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cursor.execute("ALTER TABLE whatsapp_links ADD COLUMN IF NOT EXISTS "
                       "channel TEXT NOT NULL DEFAULT 'whatsapp'")
        # The choice a chat was just offered ("reply 1, 2 or AGENT"), so the
        # next message can be read as the answer to it.
        cursor.execute("ALTER TABLE whatsapp_links ADD COLUMN IF NOT EXISTS pending TEXT")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_whatsapp_links_user "
                       "ON whatsapp_links(user_id)")
        # Meta may deliver the same message twice; each is answered once.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS whatsapp_seen (
                message_id TEXT PRIMARY KEY,
                seen_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS whatsapp_log (
                id SERIAL PRIMARY KEY,
                wa_id TEXT,
                user_id INTEGER,
                company_id INTEGER,
                question TEXT,
                intent TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
    finally:
        conn.close()


def _hash(code):
    return hashlib.sha256(str(code).strip().encode("utf-8")).hexdigest()


def start_link(user_id, company_id, channel="whatsapp"):
    """A fresh one-time code for this user and channel. Any earlier pending
    code for the same channel goes."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM whatsapp_links WHERE user_id = %s AND wa_id IS NULL "
                       "AND channel = %s", (user_id, channel))
        for _ in range(5):
            code = f"{secrets.randbelow(10 ** 6):06d}"
            cursor.execute("""
                SELECT 1 FROM whatsapp_links
                WHERE code_hash = %s AND code_expires > NOW()
            """, (_hash(code),))
            if not cursor.fetchone():
                break
        cursor.execute("""
            INSERT INTO whatsapp_links (user_id, company_id, code_hash, code_expires, channel)
            VALUES (%s, %s, %s, NOW() + (%s * INTERVAL '1 minute'), %s)
        """, (user_id, company_id, _hash(code), CODE_TTL_MINUTES, channel))
        conn.commit()
        return code
    finally:
        conn.close()


def complete_link(code, wa_id, channel="whatsapp"):
    """Link wa_id with the pending code. (user_id, company_id), or None.

    The number replaces whatever it was linked to before, and the user's
    earlier number is let go: one number per user, one user per number.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, company_id FROM whatsapp_links
            WHERE wa_id IS NULL AND code_hash = %s AND code_expires > NOW()
              AND channel = %s
        """, (_hash(code), channel))
        row = cursor.fetchone()
        if not row:
            return None
        link_id, user_id, company_id = row[0], row[1], row[2]
        cursor.execute("DELETE FROM whatsapp_links WHERE wa_id = %s OR "
                       "(user_id = %s AND channel = %s AND id <> %s)",
                       (wa_id, user_id, channel, link_id))
        cursor.execute("""
            UPDATE whatsapp_links
            SET wa_id = %s, code_hash = NULL, code_expires = NULL,
                linked_at = NOW(), last_used = NOW()
            WHERE id = %s
        """, (wa_id, link_id))
        conn.commit()
        return user_id, company_id
    finally:
        conn.close()


def _link_row(cursor, where, value, channel=None):
    extra = " AND channel = %s" if channel else ""
    params = (value, channel) if channel else (value,)
    cursor.execute(f"""
        SELECT user_id, company_id, wa_id, linked_at, last_used, last_token,
               code_expires, pending
        FROM whatsapp_links WHERE {where} = %s{extra}
        ORDER BY (wa_id IS NULL), id DESC LIMIT 1
    """, params)
    row = cursor.fetchone()
    if not row:
        return None
    try:
        pending = json.loads(row[7]) if row[7] else None
    except (TypeError, ValueError):
        pending = None
    return {"user_id": row[0], "company_id": row[1], "wa_id": row[2],
            "linked_at": row[3], "last_used": row[4], "last_token": row[5],
            "code_expires": row[6], "pending": pending}


def set_pending(wa_id, pending):
    """Remember (or, with None, forget) the choice this chat was offered."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE whatsapp_links SET pending = %s WHERE wa_id = %s",
                       (json.dumps(pending) if pending else None, wa_id))
        conn.commit()
    finally:
        conn.close()


def link_for_number(wa_id):
    conn = get_connection()
    try:
        return _link_row(conn.cursor(), "wa_id", wa_id)
    finally:
        conn.close()


def link_for_user(user_id, channel="whatsapp"):
    """The user's linked chat on a channel, else their pending code's row."""
    conn = get_connection()
    try:
        return _link_row(conn.cursor(), "user_id", user_id, channel)
    finally:
        conn.close()


def touch(wa_id, last_token=None):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if last_token:
            cursor.execute("UPDATE whatsapp_links SET last_used = NOW(), last_token = %s "
                           "WHERE wa_id = %s", (last_token, wa_id))
        else:
            cursor.execute("UPDATE whatsapp_links SET last_used = NOW() WHERE wa_id = %s",
                           (wa_id,))
        conn.commit()
    finally:
        conn.close()


def switch_company(wa_id, company_id):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE whatsapp_links SET company_id = %s, last_token = NULL "
                       "WHERE wa_id = %s", (company_id, wa_id))
        conn.commit()
    finally:
        conn.close()


def unlink_number(wa_id):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM whatsapp_links WHERE wa_id = %s", (wa_id,))
        conn.commit()
    finally:
        conn.close()


def unlink_user(user_id, channel="whatsapp"):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM whatsapp_links WHERE user_id = %s AND channel = %s",
                       (user_id, channel))
        conn.commit()
    finally:
        conn.close()


def first_sighting(message_id):
    """True the first time a message id is seen, False on a redelivery."""
    if not message_id:
        return True
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO whatsapp_seen (message_id) VALUES (%s)
            ON CONFLICT (message_id) DO NOTHING
        """, (message_id,))
        fresh = cursor.rowcount == 1
        cursor.execute("DELETE FROM whatsapp_seen WHERE seen_at < NOW() - (%s * INTERVAL '1 day')",
                       (SEEN_TTL_DAYS,))
        conn.commit()
        return fresh
    finally:
        conn.close()


def log_question(wa_id, user_id, company_id, question, intent):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO whatsapp_log (wa_id, user_id, company_id, question, intent)
            VALUES (%s, %s, %s, %s, %s)
        """, (wa_id, user_id, company_id, (question or "")[:1000], intent))
        conn.commit()
    finally:
        conn.close()
