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


# The only tool that is not a question. It writes a suggestion to a queue,
# never to the books, and needs the same permission as entering a voucher by
# hand - an agent must not become a way to post for someone who could not.
PROPOSE_TOOL = {
    "name": "propose_voucher",
    "description": (
        "Suggest a voucher for a person to review and approve. This does NOT "
        "post anything: it files a proposal that a human must approve in the "
        "application before any entry reaches the books. Use it when the user "
        "asks you to record something; then tell them a proposal is waiting "
        "for their approval. Amounts must balance - total debits equal total "
        "credits - and ledger names must match exactly (use list_ledgers)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "voucher_type": {
                "type": "string",
                "description": "Payment, Receipt, Journal, Contra, Sales, "
                               "Purchase, or another type this company uses.",
            },
            "date": {"type": "string", "description": "The date, as YYYY-MM-DD."},
            "narration": {
                "type": "string",
                "description": "A short note explaining the entry.",
            },
            "ledger_entries": {
                "type": "array",
                "description": "The lines. Debits must equal credits.",
                "items": {
                    "type": "object",
                    "properties": {
                        "ledger_name": {"type": "string"},
                        "type": {"type": "string",
                                 "description": "Debit or Credit."},
                        "amount": {"type": "number"},
                    },
                    "required": ["ledger_name", "type", "amount"],
                },
            },
        },
        "required": ["voucher_type", "date", "ledger_entries"],
    },
}

PROPOSE_PERMISSION = "vouchers"


def _may_propose():
    from . import chat_permissions as permissions

    user = permissions._user()
    if user is None:
        return False
    return user.can_access(PROPOSE_PERMISSION)


def _visible_tools():
    """Every tool this caller may actually run.

    Filtered rather than merely refused on use: an agent that cannot see a tool
    will not spend a turn trying it, and the catalogue stops being a list of
    what the company holds.
    """
    from .chat_toolkit import TOOLS
    from . import chat_permissions as permissions

    tools = [_describe(TOOLS[name]) for name in permissions.allowed_tool_names()]
    if _may_propose():
        tools.append(PROPOSE_TOOL)
    return tools


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
    """(user_id, company_id) for this request's token, or None.

    `Authorization: Bearer <token>` is the normal way. Some connector interfaces
    reserve that header for their own sign-in and will not let a person set it
    by hand, so `X-API-Key` is accepted as the same thing - it is the identical
    credential, arriving under a name the interface will allow.
    """
    from .mobile_api import _identify

    identity = _identify(request)
    if identity:
        return identity

    # A connector interface that asks for a header value rather than a
    # credential often sends the token bare, with no "Bearer " in front of
    # it. It is the same credential and it is looked up the same way; being
    # strict about the prefix only makes the setup fail in a way nobody can
    # see from the outside.
    header = (request.headers.get("Authorization") or "").strip()
    if header and " " not in header:
        identity = _identify_token(header)
        if identity:
            return identity

    api_key = (request.headers.get("X-API-Key") or "").strip()
    if not api_key:
        return None
    return _identify_token(api_key)


def _identify_token(token):
    """Look a bare token up the same way a bearer token is looked up."""
    from datetime import datetime

    from database.config import get_connection

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, company_id, expires_at FROM api_tokens "
                       "WHERE token = %s", (token,))
        row = cursor.fetchone()
        if not row:
            return None
        user_id, company_id, expires_at = row[0], row[1], row[2]
        if expires_at and expires_at < datetime.now():
            return None
        cursor.execute("UPDATE api_tokens SET last_used = NOW() WHERE token = %s",
                       (token,))
        conn.commit()
        return user_id, company_id
    except Exception:
        return None
    finally:
        conn.close()


def _reject_foreign_origin():
    """Refuse a browser on another site driving this endpoint.

    An agent connects server to server and sends no Origin at all, so the
    check only bites when a browser is involved - which, for an endpoint
    holding a company's accounts, is the case worth refusing.
    """
    origin = request.headers.get("Origin")
    if not origin:
        return None
    if origin.rstrip("/") == request.url_root.rstrip("/"):
        return None
    response = jsonify({"error": "forbidden_origin",
                        "message": "That origin may not use this endpoint."})
    response.status_code = 403
    return response


def _unauthorised(message="A valid bearer token is required."):
    _log_refusal()
    response = jsonify({"error": "unauthorized", "message": message})
    response.status_code = 401
    return response


def _log_refusal():
    """Note which headers a refused caller sent - names only, never values.

    Whether a client is sending a credential at all, and under what name, is
    otherwise invisible: the access log records the status and nothing else, and
    the difference between "sent the wrong token" and "sent no token" changes
    which end needs fixing. Values are deliberately excluded; a token in a log
    file is a token that has leaked.
    """
    try:
        interesting = [name for name in request.headers.keys()
                       if name.lower() in ("authorization", "x-api-key",
                                           "x-mcp-token", "api-key",
                                           "mcp-protocol-version", "origin",
                                           "user-agent")]
        detail = []
        for name in interesting:
            value = request.headers.get(name, "")
            if name.lower() == "authorization":
                first = value.split(" ", 1)[0].lower() if value else ""
                scheme = first if first in ("bearer", "basic", "token") \
                    else "no recognised scheme"
                detail.append(f"{name}=<{scheme}, {len(value)} chars>")
            elif name.lower() in ("x-api-key", "x-mcp-token", "api-key"):
                detail.append(f"{name}=<{len(value)} chars>")
            else:
                detail.append(f"{name}={value[:60]}")
        current_app.logger.warning(
            "MCP refused a caller. Headers seen: %s",
            "; ".join(detail) or "none of interest")
    except Exception:
        pass


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
    if name == PROPOSE_TOOL["name"]:
        return _propose(arguments, request_id, company_id)
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


def _propose(arguments, request_id, company_id):
    """File a suggested voucher. Nothing is posted here."""
    from .agent_proposals import ProposalRejected, propose_voucher

    started = time.perf_counter()
    if not _may_propose():
        _log_call("propose_voucher", company_id, started, "denied")
        return _result(request_id, {
            "content": [{"type": "text", "text":
                         "You do not have permission to enter vouchers, so an "
                         "agent acting for you cannot propose one either."}],
            "isError": True,
        })

    try:
        proposal_id, summary = propose_voucher(
            arguments, company_id, getattr(request, "mcp_user_id", None))
    except ProposalRejected as rejected:
        _log_call("propose_voucher", company_id, started, "invalid")
        return _result(request_id, {
            "content": [{"type": "text", "text": str(rejected)}],
            "isError": True,
        })
    except Exception as exc:
        _log_call("propose_voucher", company_id, started, "failed")
        current_app.logger.exception("Could not file a proposal")
        return _result(request_id, {
            "content": [{"type": "text", "text": f"That did not work: {exc}"}],
            "isError": True,
        })

    _log_call("propose_voucher", company_id, started, "ok")
    return _result(request_id, {"content": [{"type": "text", "text":
        f"Proposal #{proposal_id} filed and waiting for approval: {summary}.\n\n"
        "Nothing has been posted. Open Agent Proposals in the application to "
        "review and approve it."}]})


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

@mcp_bp.route("/.well-known/oauth-protected-resource",
               defaults={"path": ""}, methods=["GET"])
@mcp_bp.route("/.well-known/oauth-protected-resource/<path:path>",
               methods=["GET"])
@mcp_bp.route("/.well-known/oauth-authorization-server",
               defaults={"path": ""}, methods=["GET"], endpoint="no_as_metadata")
def no_oauth_metadata(path):
    """There is no OAuth flow here; this endpoint takes a token header.

    Without these routes the probes reached the sign-in guard and were
    answered with a redirect to a login page, which a client cannot read as
    a refusal and so retries.
    """
    response = jsonify({
        "error": "not_found",
        "message": "This server does not use OAuth. Configure the connector "
                   "without sign-in and send the token as an Authorization "
                   "or X-API-Key header.",
    })
    response.status_code = 404
    return response


@mcp_bp.route("/mcp", methods=["GET", "DELETE"])
def mcp_method_not_allowed():
    """The standalone SSE stream and session teardown are gone from this
    revision of the transport, and 405 is what it asks a server to say."""
    response = jsonify({"error": "method_not_allowed",
                        "message": "This endpoint accepts POST."})
    response.status_code = 405
    response.headers["Allow"] = "POST"
    return response


@mcp_bp.route("/mcp", methods=["POST"])
def mcp():
    forbidden = _reject_foreign_origin()
    if forbidden:
        return forbidden

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
    if body.get("error", {}).get("code") == METHOD_NOT_FOUND:
        # The status is what lets a client distinguish this server from a
        # legacy one that simply does not serve this path.
        response.status_code = 404
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
