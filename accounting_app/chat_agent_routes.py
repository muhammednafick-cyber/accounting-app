"""The endpoint for the agent assistant.

Kept apart from `chat_routes` on purpose. The old assistant keeps its own URL,
its own blueprint and its own code path, so this one can be switched off - or
deleted outright - without the working assistant noticing.
"""
from flask import Blueprint, jsonify, request
from flask_login import login_required

from database.company_db import get_current_company_id

from .rate_limit import rate_limit

chat_agent_bp = Blueprint("chat_agent_bp", __name__)

# A kill switch that does not need a deploy. Set the company's AI setting
# `chat_agent_enabled` to "0" and every request here is refused, whatever the
# chat window is showing.
ENABLED_SETTING = "chat_agent_enabled"


def _enabled():
    try:
        from database.ai_settings_db import get_ai_setting

        return str(get_ai_setting(ENABLED_SETTING, "1")).strip() not in ("0", "false", "off")
    except Exception:
        # A missing settings row must not take the feature down; the checkbox
        # in the chat window is the normal way to avoid it.
        return True


@chat_agent_bp.route("/api/chat_agent/invoice", methods=["POST"])
@login_required
@rate_limit(10, 300, message="Reading an invoice is a paid AI call, and a long "
                             "scan is several. Wait a few minutes and retry.")
def chat_agent_invoice():
    """Read an attached purchase invoice and file it as a proposal.

    Separate from the chat endpoint because it takes a file rather than a
    question, and because it must not be reachable when the agent is off.
    """
    from . import chat_agent as agent
    from .chat_agent_invoice import InvoiceNotUsable, invoice_to_proposal

    if not _enabled():
        return jsonify({"success": False,
                        "message": "The agent assistant is switched off for "
                                   "this company."}), 403
    if not agent._may_propose():
        return jsonify({"success": False,
                        "message": "You do not have permission to enter "
                                   "vouchers, so an invoice cannot be turned "
                                   "into one for you."}), 403

    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"success": False, "message": "No file uploaded"}), 400

    company_id = get_current_company_id()
    if not company_id:
        return jsonify({"success": False, "message": "No company is open."}), 400

    from . import chat_permissions as permissions

    user = permissions._user()
    try:
        filed = invoice_to_proposal(uploaded.read(), uploaded.filename,
                                    company_id, getattr(user, "id", None),
                                    supplier_hint=request.form.get("supplier"))
    except InvoiceNotUsable as unusable:
        # Not an error in the server's sense: the invoice was read and the
        # answer is that it cannot be entered yet, with the reason. It belongs
        # in the chat as a message, not as a failed request.
        return jsonify({"success": True, "data": {
            "intent": "invoice_not_usable",
            "response": _escape(str(unusable)),
            "data": {"source": "agent"},
            "explanation": "invoice could not be matched",
        }})
    except Exception as exc:
        from flask import current_app

        current_app.logger.exception("invoice to proposal failed")
        return jsonify({"success": False,
                        "message": "The invoice could not be read: %s" % exc}), 500

    return jsonify({"success": True, "data": _invoice_reply(filed)})


def _escape(text):
    import html

    return html.escape(str(text)).replace("\n", "<br>")


def _invoice_reply(filed):
    """The chat bubble for an invoice that became a proposal."""
    from . import chat_agent as agent

    card = agent._proposal_card({"proposal_id": filed["proposal_id"],
                                 "voucher_type": "Purchase"})
    return {
        "intent": "agent",
        "response": (
            "<b>Read invoice %s from %s</b><br>%s line(s) matched your items."
            "<br>Proposal #%s is waiting for approval: %s<br>%s"
            % (_escape(filed["invoice_number"]), _escape(filed["supplier"]),
               filed["lines"], filed["proposal_id"], _escape(filed["summary"]),
               card)),
        "data": {"source": "agent", "tool": "invoice",
                 "proposal_id": filed["proposal_id"]},
        "explanation": "invoice read into a purchase proposal",
    }


@chat_agent_bp.route("/api/chat_agent", methods=["POST"])
@login_required
@rate_limit(10, 60, message="The agent runs several AI calls per question. "
                            "Give it a moment before asking again.")
def chat_agent():
    """One question, answered by however many tools it takes.

    The rate limit is deliberately tighter than the old assistant's 30/minute:
    a question here can be six model calls rather than one.
    """
    from . import chat_agent as agent

    if not _enabled():
        return jsonify({"success": False,
                        "message": "The agent assistant is switched off for "
                                   "this company."}), 403

    data = request.get_json(silent=True) or {}
    question = (data.get("query") or "").strip()
    if not question:
        return jsonify({"success": False, "message": "No query provided"}), 400

    history = data.get("history")
    if not isinstance(history, list):
        history = []

    company_id = get_current_company_id()
    if not company_id:
        return jsonify({"success": False,
                        "message": "No company is open."}), 400

    try:
        result = agent.run(question, company_id, history=history)
    except Exception as exc:
        # Never fall through to the old assistant here. Silently answering from
        # a different engine would make the two impossible to tell apart when
        # one of them is misbehaving.
        from flask import current_app

        current_app.logger.exception("chat agent failed")
        return jsonify({"success": False,
                        "message": "The agent failed: %s" % exc}), 500

    return jsonify({"success": True, "data": result})
