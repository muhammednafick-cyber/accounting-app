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

    from flask_login import current_user

    user_id = getattr(current_user, "id", None)
    run_id = _run_id(data.get("run_id"))
    progress = _progress_writer(user_id, run_id) if run_id else None

    try:
        result = agent.run(question, company_id, history=history,
                           progress=progress)
    except Exception as exc:
        # Never fall through to the old assistant here. Silently answering from
        # a different engine would make the two impossible to tell apart when
        # one of them is misbehaving.
        from flask import current_app

        current_app.logger.exception("chat agent failed")
        return jsonify({"success": False,
                        "message": "The agent failed: %s" % exc}), 500

    if progress is not None:
        progress(None)              # finished: the poller can stop

    # Nothing looked up and nothing said is a question this could not answer,
    # and worth knowing about - see Chat Insights.
    reply_data = result.get("data") or {}
    if result.get("intent") == "agent" and not reply_data.get("tools_used"):
        from database.chat_insights_db import record_miss

        record_miss(company_id, user_id, question, "agent_no_report")

    return jsonify({"success": True, "data": result})


# ------------------------------------------------------------------ progress

import re as _re

_RUN_ID = _re.compile(r"^[0-9a-f]{16,32}$")


def _run_id(value):
    value = str(value or "").strip().lower()
    return value if _RUN_ID.match(value) else None


def _progress_key(user_id, run_id):
    # Keyed on the user as well as the run, so one person cannot read another's
    # steps by guessing an id.
    return "agentprog:%s:%s" % (user_id, run_id)


def _progress_writer(user_id, run_id):
    """A callback that records each step where any worker can read it.

    The poll may well land on a different gunicorn worker from the one running
    the agent, so the steps go to the database rather than to memory.
    """
    from database.app_state_db import chat_export_save

    key = _progress_key(user_id, run_id)
    steps = []

    def write(step):
        if step is None:
            chat_export_save(key, {"steps": steps, "done": True})
            return
        steps.append(str(step)[:120])
        chat_export_save(key, {"steps": steps, "done": False})

    return write


@chat_agent_bp.route("/api/chat_agent/progress")
@login_required
def chat_agent_progress():
    """The steps so far for one run of the agent."""
    from flask_login import current_user

    from database.app_state_db import chat_export_load

    run_id = _run_id(request.args.get("run"))
    if not run_id:
        return jsonify({"success": False, "message": "Bad run id"}), 400
    state = chat_export_load(_progress_key(getattr(current_user, "id", None),
                                           run_id)) or {}
    return jsonify({"success": True, "steps": state.get("steps") or [],
                    "done": bool(state.get("done"))})
