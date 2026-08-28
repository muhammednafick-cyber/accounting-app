"""Server-side conversation state for the chat assistant.

The chat widget posts one question at a time and keeps nothing but the text of
the bubbles, so a follow-up like "and for last month?" or "break it by
customer" has nothing to work from. This module keeps, per browser session,
what the last answer actually was: which tool ran, with which resolved
arguments, and the export token for its rows.

State lives in PostgreSQL (see database.app_state_db) rather than in memory, so
it survives a worker recycle and is shared across workers - a follow-up reaches
the same conversation whichever process happens to serve it. Losing it costs
the user a re-phrase, never a wrong answer, so it is trimmed on a TTL.

get() returns a snapshot. Callers outside this module only read it; every
mutation goes through the functions here, which load, change and save.
"""
import uuid

MAX_TURNS = 12
SESSION_KEY = "chat_ctx_id"


def _blank():
    return {
        "turns": [],          # [{question, tool, args, token, columns, rows}]
        "last_tool": None,
        "last_args": {},
        "last_period": None,   # (start, end, label)
        "last_ledger": None,
        "last_item": None,
        "last_voucher_type": None,
        "last_token": None,
        "pending": None,       # {"kind": "permission"|"clarify"|"missing", ...}
    }


def _session_id():
    """The id for the current request's session, creating one if needed."""
    from flask import session

    try:
        sid = session.get(SESSION_KEY)
        if not sid:
            sid = uuid.uuid4().hex
            session[SESSION_KEY] = sid
        return sid
    except RuntimeError:
        return None  # No request context (tests, CLI)


def _load(sid):
    from database.app_state_db import chat_session_load

    try:
        state = chat_session_load(sid)
    except Exception:
        # A conversation is a convenience, never the answer itself. If the
        # store is unreachable, carry on without continuity rather than
        # failing the question the user actually asked.
        return _blank()
    if not state:
        return _blank()
    merged = _blank()
    merged.update(state)
    return merged


def _save(sid, state):
    from database.app_state_db import chat_session_save

    try:
        chat_session_save(sid, state)
    except Exception:
        pass


def get():
    """The conversation state for this session. Always returns a dict."""
    sid = _session_id()
    if sid is None:
        return _blank()
    return _load(sid)


def reset():
    """Forget everything about this conversation."""
    sid = _session_id()
    if sid is None:
        return
    _save(sid, _blank())


def record_turn(question, tool, args, result, token=None):
    """Remember a completed answer so the next question can build on it."""
    sid = _session_id()
    if sid is None:
        return
    state = _load(sid)

    state["turns"].append({
        "question": question,
        "tool": tool,
        "args": dict(args or {}),
        "token": token,
        "columns": (result or {}).get("columns"),
        "rows": len((result or {}).get("rows") or []),
        "title": (result or {}).get("title"),
    })
    del state["turns"][:-MAX_TURNS]

    state["last_tool"] = tool
    state["last_args"] = dict(args or {})
    state["last_token"] = token or state.get("last_token")

    args = args or {}
    if args.get("start") or args.get("end"):
        state["last_period"] = (args.get("start"), args.get("end"),
                                args.get("period_label"))
    for key, slot in (("ledger_name", "last_ledger"),
                      ("item_name", "last_item"),
                      ("voucher_type", "last_voucher_type")):
        if args.get(key):
            state[slot] = args[key]
    state["pending"] = None

    _save(sid, state)


def set_pending(kind, **payload):
    """Park a question back at the user (permission, clarification, a date)."""
    pending = dict(payload, kind=kind)
    sid = _session_id()
    if sid is None:
        return pending
    state = _load(sid)
    state["pending"] = pending
    _save(sid, state)
    return pending


def take_pending():
    """Read and clear whatever the assistant was waiting for."""
    sid = _session_id()
    if sid is None:
        return None
    state = _load(sid)
    pending = state.get("pending")
    if pending is not None:
        state["pending"] = None
        _save(sid, state)
    return pending


def peek_pending():
    return get().get("pending")


def history_for_prompt(limit=6):
    """Recent turns, condensed, for the tool-picker prompt."""
    turns = get()["turns"][-limit:]
    lines = []
    for t in turns:
        args = ", ".join(
            f"{k}={v}" for k, v in (t.get("args") or {}).items()
            if v is not None and k != "period_label"
        )
        lines.append(f"Q: {t['question']}\n   -> {t['tool']}({args}) [{t['rows']} rows]")
    return "\n".join(lines)
