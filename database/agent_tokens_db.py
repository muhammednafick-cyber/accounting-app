"""Tokens an AI agent connects with, and the rules about who may have one.

An agent token is a standing credential to a company's books, so the rules
matter more than the plumbing:

  * it belongs to one person and one company, both fixed when it is issued -
    an agent cannot ask to be someone else or to look at another company;
  * it can never carry more access than the person it belongs to, because the
    permission check happens per call against that user, not against the token;
  * nobody may issue one for a company they cannot already open themselves;
  * the value is shown once and never again - only a short prefix is stored in
    view, so a token that leaks is replaced rather than recovered.

The same functions serve the screen a user manages their own tokens on and the
command-line tool an administrator uses, so the rules cannot drift apart.
"""
import secrets
from datetime import datetime, timedelta

from .config import get_connection

# A phone re-authenticates by signing in again; an agent token sits in a
# connector's settings, where an expiry every month would be an annoyance
# people work around by never revoking anything.
AGENT_TOKEN_DAYS = 365

# What marks a row in api_tokens as belonging to an agent rather than a phone.
AGENT_PREFIX = "MCP · "


class NotPermitted(Exception):
    """The user may not have a token for that company."""


def _may_access(user_id, company_id):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT is_admin FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        if not row:
            return False
        if row[0]:
            return True
        cursor.execute("SELECT 1 FROM user_company_access "
                       "WHERE user_id = %s AND company_id = %s",
                       (user_id, company_id))
        return cursor.fetchone() is not None
    finally:
        conn.close()


def issue_agent_token(user_id, company_id, label):
    """Create a token for this user and company. Returns the value, once.

    Refuses a company the user cannot already open. Without that check the
    screen would be a way to grant yourself access the application denies you.
    """
    if not _may_access(user_id, company_id):
        raise NotPermitted("You do not have access to that company.")

    token = secrets.token_urlsafe(32)
    device = (AGENT_PREFIX + (label or "agent").strip())[:120]
    expires = datetime.now() + timedelta(days=AGENT_TOKEN_DAYS)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO api_tokens (token, user_id, company_id, device, expires_at)
            VALUES (%s, %s, %s, %s, %s)
        """, (token, user_id, company_id, device, expires))
        conn.commit()
    finally:
        conn.close()
    return token, expires


def list_agent_tokens(user_id):
    """This user's agent tokens. Never the value - only enough to recognise one."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT t.id, t.token, t.device, c.name, t.created_at,
                   t.expires_at, t.last_used
            FROM api_tokens t
            LEFT JOIN companies c ON c.id = t.company_id
            WHERE t.user_id = %s AND t.device LIKE %s
            ORDER BY t.id DESC
        """, (user_id, AGENT_PREFIX + "%"))
        return [{
            "id": row[0],
            "prefix": (row[1] or "")[:8],
            "label": (row[2] or "")[len(AGENT_PREFIX):] or "agent",
            "company": row[3] or "—",
            "created_at": row[4],
            "expires_at": row[5],
            "last_used": row[6],
        } for row in cursor.fetchall()]
    finally:
        conn.close()


def revoke_agent_token(user_id, token_id):
    """Delete one of this user's own tokens. True when something was removed.

    Scoped to the owner: a token id from somebody else's screen must not be
    revocable by guessing the number.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM api_tokens
            WHERE id = %s AND user_id = %s AND device LIKE %s
        """, (token_id, user_id, AGENT_PREFIX + "%"))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def companies_for(user_id):
    """The companies this user may issue a token for."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT is_admin FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        if row and row[0]:
            cursor.execute("SELECT id, name FROM companies ORDER BY name")
        else:
            cursor.execute("""
                SELECT c.id, c.name FROM companies c
                JOIN user_company_access a ON a.company_id = c.id
                WHERE a.user_id = %s ORDER BY c.name
            """, (user_id,))
        return [{"id": r[0], "name": r[1]} for r in cursor.fetchall()]
    finally:
        conn.close()
