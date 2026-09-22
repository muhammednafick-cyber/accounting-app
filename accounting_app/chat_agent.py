"""A second chat assistant that keeps going instead of answering once.

The existing assistant (`chat_router`) answers one message with one tool: the
model picks a report, the report runs, the table is shown. That is deliberate
and it stays exactly as it is - nothing in this file is reachable unless the
user ticks "Agent" in the chat window.

This one runs the same tools in a loop. The model calls a report, reads what
came back, and decides whether it needs another before it answers. That is the
only real difference, and it is what lets it handle a question that needs three
reports and some arithmetic in between.

Two rules carried over from the old assistant, because they are the reason it
can be trusted:

  * No figure comes from the model. Every table shown to the user is rendered
    from the tool's own output, untouched. The model writes only the sentences
    between the tables, and they are labelled as written by AI.
  * A tool the user may not run is never offered and never executed. The
    catalogue is filtered by `chat_permissions`, and `chat_toolkit.run` checks
    again on the way in.

It is read-only. It cannot propose or post anything; that path exists only
over MCP, where a human approves each suggestion on the voucher screen.
"""
import json
import re
import time

from . import chat_permissions as P
from . import chat_router as CR
from . import chat_toolkit as TK

# How many times round the loop before we stop and answer with what we have.
# Six covers "compare these two things and tell me what moved"; past that the
# question is usually one the model has misunderstood, and every extra step is
# another call the company pays for.
MAX_STEPS = 6

# Rows fed back to the model per tool call. The user still sees every row - this
# cap is only what goes back into the conversation, so a 4,000-row ledger dump
# does not blow the context window (and the bill) on step two.
FEEDBACK_ROWS = 40

SYSTEM_PROMPT = """You are an accounting assistant inside a live accounting \
application. You answer questions about this one company's own books.

Use the tools to get facts. Never state a figure that did not come back from a \
tool in this conversation - not from memory, not estimated, not carried over \
from another company. If the tools cannot answer, say so plainly.

Work in steps. Call a tool, read what it returns, and call another if you need \
more before answering. When you have enough, write a short answer in plain \
English: two or three sentences, no tables. The application shows the user the \
full tables from every tool you called, so do not repeat the rows - explain \
what they mean, point at what matters, and mention anything that looks wrong.

Dates in this company are day-month-year. Amounts are in the company's own \
currency; do not convert them.

You cannot create, change, delete or reverse anything, and you must not claim \
you have. If the user asks you to record an entry, tell them to use the \
voucher screen."""


class AgentUnavailable(Exception):
    """No key, no model, or the provider refused - nothing was answered."""


# ============================================================
# The tool catalogue
# ============================================================

def _catalogue():
    """The tools this user may run, in OpenAI/OpenRouter function form.

    Built from the same registry and the same permission filter the MCP server
    uses, so Claude and this assistant are always offered the same things.
    """
    from .mcp_routes import _describe

    tools = []
    for name in P.allowed_tool_names():
        described = _describe(TK.TOOLS[name])
        tools.append({
            "type": "function",
            "function": {
                "name": described["name"],
                "description": described["description"],
                "parameters": described["inputSchema"],
            },
        })
    if P.can_use_ai_sql():
        tools.append(SQL_TOOL)
    return tools


# The escape hatch, offered only to users who already have the broad reporting
# permission. In the old assistant this was a last resort reached after asking
# the user; here it is one more tool, so the model can answer most of a question
# from a proper report and fetch only the missing piece this way.
SQL_TOOL = {
    "type": "function",
    "function": {
        "name": "query_database",
        "description": (
            "Ask a question that none of the other tools cover. It is turned "
            "into a read-only database query. Slower and less reliable than a "
            "real report, so try the other tools first. Never use it for "
            "something a named report already answers."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question in plain English, with the "
                                   "period and any names spelled out in full.",
                },
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    },
}


# ============================================================
# Running one tool
# ============================================================

def _run_sql_tool(arguments, company_id):
    """The text-to-SQL tool, in the shape of a toolkit result."""
    from . import ai_sql

    question = (arguments.get("question") or "").strip()
    if not question:
        return {"message": "No question was given.", "isError": True}

    result = ai_sql.answer_from_database(question, company_id)
    if result.get("error"):
        return {"message": "The query failed: " + str(result["error"]),
                "isError": True}

    data = result.get("data") or {}
    rows, columns = data.get("rows"), data.get("columns")
    if rows and columns:
        return {
            "title": "Database query - " + question[:60],
            "columns": columns,
            "rows": rows,
            "summary": result.get("response") or "",
            "tool": "query_database",
            "generated": True,
        }
    return {
        "title": "Database query - " + question[:60],
        "summary": result.get("response") or "The query returned nothing.",
        "tool": "query_database",
        "generated": True,
    }


def _run_tool(name, arguments, company_id):
    """Execute one tool call. Never raises: the model is told what went wrong.

    A failure has to come back as a tool result rather than an exception,
    otherwise one mistyped ledger name ends the whole conversation instead of
    letting the model look the name up and try again.
    """
    if name == "query_database":
        if not P.can_use_ai_sql():
            return {"message": "You do not have access to Reports, so this "
                               "cannot be used.", "isError": True}
        try:
            return _run_sql_tool(arguments, company_id)
        except Exception as exc:
            return {"message": "The query failed: " + str(exc), "isError": True}

    if name not in TK.TOOLS:
        return {"message": "No such tool: " + str(name), "isError": True}

    try:
        result, _resolved = TK.run(name, arguments, company_id=company_id)
        return result
    except P.PermissionDenied as denied:
        return {"message": "You do not have permission to run this: it needs "
                           "access to " + str(denied.label) + ".",
                "isError": True}
    except Exception as exc:
        return {"message": "That did not work: " + str(exc), "isError": True}


def _feedback(result):
    """What the model sees after a tool call: the same text MCP sends Claude.

    Long results are cut down - the model is told how many rows it is not
    seeing, so it does not read a truncated list as the whole answer.
    """
    from .mcp_routes import _render

    rows = result.get("rows") or []
    trimmed = dict(result)
    note = None
    if len(rows) > FEEDBACK_ROWS:
        trimmed["rows"] = rows[:FEEDBACK_ROWS]
        note = ("Showing the first %d of %d rows. The user can see all of them; "
                "use the totals rather than adding these up."
                % (FEEDBACK_ROWS, len(rows)))

    text = _render(trimmed)["content"][0]["text"]
    if note:
        text += "\n\n" + note
    return text


# ============================================================
# The model
# ============================================================

def _model():
    """The agent's model, which is deliberately its own setting.

    Choosing well, step after step, is the hard part of this loop and is where a
    cheap model shows. Pointing the agent at a stronger model must not change
    what the old assistant costs, so it does not share that setting.
    """
    from database.ai_settings_db import get_ai_setting
    from .chatbot_service import get_openrouter_model

    return get_ai_setting("openrouter_agent_model", None) or get_openrouter_model()


def _ask_model(messages, tools):
    from .chatbot_service import (OPENROUTER_URL, get_openrouter_api_key,
                                  openrouter_request)

    api_key = get_openrouter_api_key()
    if not api_key:
        raise AgentUnavailable(
            "No OpenRouter API key is configured. Add one in AI Settings.")

    payload = {
        "model": _model(),
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": 0,
    }
    headers = {"Authorization": "Bearer " + api_key,
               "Content-Type": "application/json"}

    body, error = openrouter_request(payload, headers)
    if error:
        raise AgentUnavailable(str(error))
    try:
        return body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise AgentUnavailable("The model returned nothing usable.")


def _arguments(call):
    """Arguments off a tool call, which arrive as a JSON string."""
    raw = (call.get("function") or {}).get("arguments")
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        return {}


# ============================================================
# "give me that in excel"
# ============================================================

# A download is not a question, and it must not cost a model call or depend on
# the model thinking to offer one. The old assistant answers these itself; so
# does this one, from the last result it parked.
# A phrase counts as an export when every word in it is either a filler word
# or the name of a format. Matching that way rather than with one long pattern
# keeps "give me that in excel" and "csv" working without also swallowing
# "show me the excel import log", which has real words in it too.
_EXPORT_FILLER = {
    "and", "also", "now", "then", "ok", "okay", "please", "give", "send",
    "show", "get", "put", "make", "save", "me", "it", "that", "this", "them",
    "the", "previous", "last", "result", "results", "data", "above", "one",
    "in", "as", "to", "into", "a", "an", "with", "of", "copy", "version",
    "format", "want", "i", "can", "you",
}
_EXPORT_FORMATS = {
    "excel", "xlsx", "xls", "spreadsheet", "csv", "pdf", "workbook", "sheet",
    "download", "export", "file",
}


def _is_export_request(question):
    words = re.findall(r"[a-z]+", (question or "").lower())
    if not words:
        return False
    if not any(word in _EXPORT_FORMATS for word in words):
        return False
    return all(word in _EXPORT_FILLER or word in _EXPORT_FORMATS
               for word in words)


def _export_previous(question):
    """The download links for whatever was answered last, or None.

    Returns None when the message is not an export request, or when there is
    nothing parked to export - the caller then treats it as a normal question.
    """
    if not _is_export_request(question):
        return None

    from flask import session

    from .chat_export_store import SESSION_KEY, load

    try:
        token = session.get(SESSION_KEY)
    except RuntimeError:
        token = None
    if not token or not load(token):
        return None

    fmt = CR.requested_format(question)
    return CR.plain(
        "Here is the last result you asked for.<br>"
        + CR.export_links(token, primary=fmt),
        "export_chat_result", {"export_token": token})


# ============================================================
# The loop
# ============================================================

def run(question, company_id, history=None):
    """Answer one message, calling as many tools as it takes.

    Returns the same envelope as the old assistant - {intent, response, data,
    explanation} - so the chat window renders it without knowing which
    assistant produced it.
    """
    question = (question or "").strip()
    if not question:
        return CR.plain("Ask me anything about your masters, vouchers or reports.")

    exported = _export_previous(question)
    if exported is not None:
        return exported

    tools = _catalogue()
    if not tools:
        return CR.plain(
            "You don't have access to any of the reports I can read, so I "
            "can't answer questions about this company's data.",
            "permission_denied")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in (history or [])[-6:]:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content[:1500]})
    messages.append({"role": "user", "content": question})

    used = []        # (tool name, result) in the order they ran
    started = time.perf_counter()

    for step in range(MAX_STEPS):
        try:
            reply = _ask_model(messages, tools)
        except AgentUnavailable as exc:
            if used:
                # Tools already ran, so there is something real to show even
                # though the model cannot write the words around it.
                return _compose(question, used, None,
                                "The model became unavailable partway through, "
                                "so here is what was gathered.")
            return CR.plain("The AI service is unavailable: " + str(exc), "error")

        calls = reply.get("tool_calls") or []
        if not calls:
            return _compose(question, used, reply.get("content"), None,
                            steps=step + 1,
                            seconds=time.perf_counter() - started)

        messages.append({
            "role": "assistant",
            "content": reply.get("content") or "",
            "tool_calls": calls,
        })
        for call in calls:
            name = (call.get("function") or {}).get("name")
            result = _run_tool(name, _arguments(call), company_id)
            if not result.get("isError"):
                used.append((name, result))
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id"),
                "name": name,
                "content": _feedback(result),
            })

    # Out of steps. Everything gathered is still shown; only the closing
    # sentence is missing, and saying so is better than inventing one.
    return _compose(question, used, None,
                    "I stopped after %d steps. Here is what I found - ask me "
                    "something narrower if this isn't it." % MAX_STEPS,
                    steps=MAX_STEPS, seconds=time.perf_counter() - started)


# ============================================================
# The answer
# ============================================================

def _compose(question, used, prose, fallback_note, steps=None, seconds=None):
    """Build the chat bubble: real tables, then the model's words.

    The tables are rendered from the tools' own output by the same code the old
    assistant uses. The model's text is kept separate and labelled, so a reader
    can always see which part of the answer was computed and which was written.
    """
    parts = []
    token = None
    fmt = CR.requested_format(question)

    for name, result in used:
        block = []
        title = result.get("title")
        summary = result.get("summary")
        if title:
            block.append("<b>" + str(title) + "</b>")
        if summary and summary != title:
            block.append(str(summary))
        table = CR.render_table(result)
        if table:
            block.append(table)
            totals = CR.render_totals(result)
            if totals:
                block.append(totals)
        if result.get("note"):
            block.append("<small class='rv-note'>" + str(result["note"]) + "</small>")

        # Each table carries its own download. An answer here can hold three of
        # them, so a single button at the bottom would quietly hand over
        # whichever table happened to run last - not the one being pointed at.
        this_token = CR.remember_result(result, question)
        if this_token:
            token = this_token
            block.append(CR.export_links(this_token, primary=fmt))
        if block:
            parts.append("<br>".join(block))

    text = (prose or "").strip() or (fallback_note or "")
    if text:
        parts.append(
            "<div class='rv-agent-say'>" + _escape_prose(text) + "</div>"
            "<small class='rv-src rv-src-ai'>Written by AI from the figures "
            "above</small>")

    if used:
        names = ", ".join(sorted({n for n, _ in used}))
        parts.append("<small class='rv-src'>Computed from your data &middot; "
                     + names + "</small>")
    elif not text:
        parts.append("I couldn't find anything for that.")

    return {
        "intent": "agent",
        "response": "<br>".join(p for p in parts if p),
        "data": {
            "tool": "agent",
            "source": "agent",
            "tools_used": [n for n, _ in used],
            "steps": steps,
            "seconds": round(seconds, 2) if seconds else None,
            "export_token": token,
        },
        "explanation": "agent loop over the coded tools",
    }


def _escape_prose(text):
    """The model's words, as words.

    Its output is prose, not markup: anything angle-bracketed in it is either a
    mistake or an attempt to put markup into the page, and neither should be
    rendered. Line breaks are kept because the model writes in paragraphs.
    """
    import html

    return html.escape(str(text)).replace("\n", "<br>")
