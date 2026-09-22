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
