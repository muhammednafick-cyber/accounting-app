"""Holds the most recent tabular chat answer so it can be exported.

The chat widget is stateless between turns, so a follow-up like "give it in
excel" has nothing to work from. Each tabular answer is parked here under a
random token kept in the Flask session; the export route trades the token back
for the rows.

Results live in PostgreSQL (see database.app_state_db) rather than in memory,
so the download still works after a worker recycle and whichever worker serves
the export can find it. Rows are trimmed on a TTL - a stale token simply
returns None, which the export route already treats as "ask again".
"""
import uuid

SESSION_KEY = "chat_export_token"


def save(columns, rows, title="chat_result"):
    """Park a result set. Returns the token used to fetch it back."""
    from database.app_state_db import chat_export_save

    token = uuid.uuid4().hex
    payload = {
        "columns": list(columns),
        "rows": [list(r) for r in rows],
        "title": title,
    }
    try:
        chat_export_save(token, payload)
    except Exception:
        # Failing to cache must not fail the answer the user just asked for;
        # the export link will report that it expired.
        pass
    return token


def load(token):
    """The stored result for a token, or None if it expired or never existed."""
    if not token:
        return None
    from database.app_state_db import chat_export_load

    try:
        return chat_export_load(token)
    except Exception:
        return None


def remember(columns, rows, title="chat_result"):
    """Save a result and record its token in the current Flask session."""
    from flask import session

    token = save(columns, rows, title)
    try:
        session[SESSION_KEY] = token
    except RuntimeError:
        pass  # No request context (tests, CLI) - the token is still returned.
    return token


def current():
    """The result the current session last saw, if any."""
    from flask import session

    try:
        return load(session.get(SESSION_KEY))
    except RuntimeError:
        return None
