"""The thumbs on each chat answer, and the page that makes use of them.

Chat Insights lists what people asked that nothing could answer, most asked
first, and the answers they marked wrong. The first list is where the free,
no-AI path grows: each common miss is a phrasing worth teaching it.
"""
from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from database.company_db import get_current_company_id

chat_insights_bp = Blueprint("chat_insights_bp", __name__)

WINDOWS = (7, 30, 90)


def may_view_insights(user=None):
    """Admins and principals: what the whole company asked is not for everyone."""
    user = user or current_user
    return bool(getattr(user, "is_admin", False)
                or getattr(user, "is_principal", False))


@chat_insights_bp.route("/api/chat_feedback", methods=["POST"])
@login_required
def chat_feedback():
    from database.chat_insights_db import record_feedback

    data = request.get_json(silent=True) or {}
    vote = str(data.get("vote") or "").strip().lower()
    question = str(data.get("question") or "").strip()
    if vote not in ("up", "down") or not question:
        return jsonify({"success": False, "message": "A vote and a question are required."}), 400

    company_id = get_current_company_id()
    if not company_id:
        return jsonify({"success": False, "message": "No company is open."}), 400

    record_feedback(company_id, getattr(current_user, "id", None), question,
                    data.get("tool"), vote)
    return jsonify({"success": True})


@chat_insights_bp.route("/settings/chat-insights")
@login_required
def chat_insights():
    if not may_view_insights():
        abort(403)
    company_id = get_current_company_id()
    if not company_id:
        abort(400)

    from database.chat_insights_db import feedback_summary, top_misses

    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        days = 30
    if days not in WINDOWS:
        days = 30

    return render_template(
        "chat_insights.html",
        days=days, windows=WINDOWS,
        misses=top_misses(company_id, days=days),
        feedback=feedback_summary(company_id, days=days))
