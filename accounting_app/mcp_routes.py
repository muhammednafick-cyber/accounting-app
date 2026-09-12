"""An MCP endpoint, so an agent can read the books over a standard protocol.

The tools are the ones the in-app assistant already uses: 77 read-only
questions about ledgers, balances, stock and vouchers. Nothing here can post,
alter or reverse anything - `chat_toolkit` contains no write statements, and
this module adds none.

Three things guard it:

  * a bearer token, which names a user and a company and cannot be swapped for
    another company's;
  * the app's own menu permissions, applied through `chat_toolkit.run()` exactly
    as they are for the browser, so an agent can never read what the person
    behind it could not open by hand;
  * a per-token rate limit and a log of every call.

Protocol notes. The current revision (2026-07-28) negotiates the version on
every request through `_meta` and requires `server/discover`. Older clients
instead open with an `initialize` handshake. Both are answered here, because
which one a given client sends is not ours to choose.
"""
import html
import json
import re
import time

from flask import Blueprint, current_app, jsonify, request, session

from database.app_state_db import rate_limit_check

mcp_bp = Blueprint("mcp_bp", __name__)

SERVER_NAME = "prodata-accounts"
SERVER_VERSION = "1.0.0"

# Newest first. The tool surface is identical across these: only the handshake
# and where the version is declared differ.
SUPPORTED_VERSIONS = ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26"]

META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

INSTRUCTIONS = (
    "Read-only access to a live accounting system. Tools answer questions about "
    "ledgers, balances, customers, suppliers, stock, vouchers and financial "
    "reports for one company. Amounts are in the company's own currency and "
    "dates are day-month-year. Nothing here can create or change a record."
)

# Generous for a person asking questions, tight enough that a loop cannot sit
# on the database. Counted per token.
CALLS_PER_WINDOW = 120
WINDOW_SECONDS = 60


# ------------------------------------------------------------------ JSON-RPC

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _result(request_id, payload):
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id, code, message, data=None):
    body = {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}
    if data is not None:
        body["error"]["data"] = data
    return body


# --------------------------------------------------------------------- tools

def _input_schema(tool):
    """A JSON Schema for one toolkit tool.

    The toolkit takes free text for every argument and resolves it itself -
    "last month", "Sundry Debtors", "top 5" - so every property is a string and
    the resolver does the interpreting. Declaring narrower types here would
    reject arguments the toolkit handles perfectly well.
    """
    properties = {}
    for name in tool.param_names:
        properties[name] = {
            "type": "string",
            "description": _PARAM_HINTS.get(name, f"The {name.replace('_', ' ')}."),
        }
    return {
        "type": "object",
        "properties": properties,
        "required": list(tool.required),
        "additionalProperties": False,
    }


_PARAM_HINTS = {
    "period": "A period in words or dates, e.g. 'last month', 'this year', "
              "'01-04-2026 to 30-06-2026'. Defaults to the current year.",
    "ledger": "The exact ledger (account) name.",
    "item": "The exact stock item name.",
    "group": "An account group name, e.g. 'Sundry Debtors'.",
    "limit": "How many rows to return, as a number.",
    "party": "A customer or supplier ledger name.",
    "location": "A location name, when the company keeps more than one.",
    "date": "A single date, day-month-year.",
    "voucher_type": "A voucher type, e.g. Sales, Purchase, Receipt, Payment.",
}


def _describe(tool):
    description = tool.desc or ""
    if tool.examples:
        description += "\n\nFor example: " + "; ".join(
            f'"{e}"' for e in tool.examples[:3])
    return {
        "name": tool.name,
        "description": description.strip(),
        "inputSchema": _input_schema(tool),
    }


def _visible_tools():
    """Every tool this caller may actually run.

    Filtered rather than merely refused on use: an agent that cannot see a tool
    will not spend a turn trying it, and the catalogue stops being a list of
    what the company holds.
    """
    from .chat_toolkit import TOOLS
    from . import chat_permissions as permissions

    return [_describe(TOOLS[name]) for name in permissions.allowed_tool_names()]


_TAG = re.compile(r"<[^>]+>")


def _plain(value):
    """Toolkit prose without its markup.

    Summaries are written for the in-app chat window and carry `<b>` around the
    figures. A model reading "total debit <b>618,603.47</b>" will happily repeat
    the tags at the user, so they come off here.
    """
    if value is None:
        return ""
    return html.unescape(_TAG.sub("", str(value))).strip()


def _render(result):
    """Turn a toolkit result into MCP content.

    The toolkit answers with a title, columns, rows and a totals mapping. Text
    is what every client can render, so the table is written out plainly rather
    than shipped as a structure only some clients understand.
    """
    lines = []
    for key in ("title", "summary"):
        text = _plain(result.get(key))
        if text:
            lines.append(text)

    columns = [_plain(c) for c in (result.get("columns") or [])]
    rows = result.get("rows") or []
    if columns and rows:
        lines.append("")
        lines.append(" | ".join(columns))
        lines.append(" | ".join("-" * max(3, len(c)) for c in columns))
        for row in rows:
            lines.append(" | ".join(_plain(cell) for cell in row))

    # totals is a mapping of label to value - "Total Debit": "618,603.47" - not
    # a row. Iterating it as a row printed the labels and dropped every figure.
    totals = result.get("totals") or {}
    if isinstance(totals, dict) and totals:
        lines.append("")
        lines.append("Totals: " + ", ".join(
            f"{_plain(label)}: {_plain(value)}" for label, value in totals.items()))
    elif totals:
        lines.append("")
        lines.append("Totals: " + ", ".join(_plain(cell) for cell in totals))

    message = _plain(result.get("message"))
    if message and not rows:
        lines.append(message)
    note = _plain(result.get("note"))
    if note:
        lines.append("")
        lines.append(note)

    text = "\n".join(lines).strip() or "No data."
    return {"content": [{"type": "text", "text": text}]}


# ---------------------------------------------------------------- the caller

def _caller():
    """(user_id, company_id) for this request's bearer token, or None."""
    from .mobile_api import _identify
    return _identify(request)


def _unauthorised(message="A valid bearer token is required."):
    response = jsonify({"error": "unauthorized", "message": message})
    response.status_code = 401
    response.headers["WWW-Authenticate"] = 'Bearer realm="prodata-accounts"'
    return response


# ---------------------------------------------------------------- despatch

def _handle(method, params, request_id, company_id):
    if method == "server/discover":
        return _result(request_id, {
            "resultType": "complete",
            "supportedVersions": SUPPORTED_VERSIONS,
            "capabilities": {"tools": {}},
            "instructions": INSTRUCTIONS,
            "_meta": {META_SERVER_INFO: {"name": SERVER_NAME,
                                         "version": SERVER_VERSION}},
        })

    if method == "initialize":
        # The pre-2026-07-28 handshake. Answer in the version the client asked
        # for when it is one we speak, so an older client is not forced to
        # downgrade to our default.
        asked = (params or {}).get("protocolVersion")
        version = asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return _result(request_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS,
        })

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        return _result(request_id, {"tools": _visible_tools()})

    if method == "tools/call":
        return _call_tool(params or {}, request_id, company_id)

    return _error(request_id, METHOD_NOT_FOUND, f"Unknown method: {method}")


def _call_tool(params, request_id, company_id):
    from . import chat_permissions as permissions
    from .chat_toolkit import TOOLS, run

    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not name:
        return _error(request_id, INVALID_PARAMS, "A tool name is required.")
    if name not in TOOLS:
        return _error(request_id, INVALID_PARAMS, f"No such tool: {name}")

    started = time.perf_counter()
    try:
        result, _resolved = run(name, arguments, company_id=company_id)
    except permissions.PermissionDenied as denied:
        # An error inside the result, not a JSON-RPC error: the agent should be
        # able to tell the user why and move on, rather than treat it as a
        # transport failure.
        _log_call(name, company_id, started, "denied")
        return _result(request_id, {
            "content": [{"type": "text", "text":
                         f"You do not have permission to run this: it needs "
                         f"access to {denied.label}."}],
            "isError": True,
        })
    except Exception as exc:
        _log_call(name, company_id, started, "failed")
        current_app.logger.exception("MCP tool %s failed", name)
        return _result(request_id, {
            "content": [{"type": "text", "text": f"That did not work: {exc}"}],
            "isError": True,
        })

    _log_call(name, company_id, started, "ok")
    return _result(request_id, _render(result))


def _log_call(tool_name, company_id, started, outcome):
    """A record of what was asked, so 'what did the agent read' has an answer."""
    from database.mcp_log_db import record_mcp_call

    try:
        record_mcp_call(
            user_id=getattr(request, "mcp_user_id", None),
            company_id=company_id,
            tool_name=tool_name,
            outcome=outcome,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except Exception:
        # The log must never be the reason an answer fails to arrive.
        current_app.logger.warning("Could not record the MCP call", exc_info=True)


# ---------------------------------------------------------------- the route

@mcp_bp.route("/mcp", methods=["POST"])
def mcp():
    identity = _caller()
    if not identity:
        return _unauthorised()
    user_id, company_id = identity

    allowed, retry_after = rate_limit_check(
        "mcp", f"user:{user_id}", CALLS_PER_WINDOW, WINDOW_SECONDS)
    if not allowed:
        response = jsonify({"error": "rate_limited",
                            "message": "Too many requests. Please slow down."})
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response

    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify(_error(None, PARSE_ERROR, "The body is not valid JSON.")), 400
    if not isinstance(payload, dict):
        return jsonify(_error(None, INVALID_REQUEST,
                              "A single JSON-RPC request object is expected.")), 400

    method = payload.get("method")
    request_id = payload.get("id")
    if not method:
        return jsonify(_error(request_id, INVALID_REQUEST, "No method given.")), 400

    # Permissions and the company are resolved from the token, never from the
    # request body - an agent cannot ask to be someone else.
    from . import chat_permissions as permissions
    permissions.use_user(user_id)
    request.mcp_user_id = user_id
    _pin_company(company_id)

    # A notification carries no id and expects no reply.
    if request_id is None and str(method).startswith("notifications/"):
        return "", 202

    body = _handle(method, payload.get("params"), request_id, company_id)
    response = jsonify(body)
    response.headers["MCP-Protocol-Version"] = _negotiated_version(payload)
    return response


def _negotiated_version(payload):
    asked = ((payload.get("params") or {}).get("_meta") or {}).get(META_VERSION) \
        or request.headers.get("MCP-Protocol-Version")
    return asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]


def _pin_company(company_id):
    """Make the toolkit see this token's company, and no other.

    Several tools read the company from the session the way a browser request
    would. Writing it here keeps those paths identical rather than special-casing
    them, and clearing `modified` stops Flask attaching a session cookie to a
    reply no agent will ever send back.
    """
    session["company_id"] = company_id
    session.modified = False
