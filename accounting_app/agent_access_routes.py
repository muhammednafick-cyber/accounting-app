"""Where a person manages the tokens their own AI agent connects with.

Self-service on purpose. The alternative - an administrator generating tokens
and sending them out - means a standing credential to a company's books
travelling by email or chat, where it stays readable long after anyone
remembers it was sent. Here the token is created by the person who will use it,
shown once on their own screen, and revoked by them the moment they want it
gone.

Nobody needs a special permission to reach this page: a token can only ever
carry the access its owner already has, so managing one is not a privilege
beyond having an account.
"""
from flask import (Blueprint, flash, redirect, render_template, request,
                   session, url_for)
from flask_login import current_user, login_required

from database.agent_tokens_db import (NotPermitted, companies_for,
                                      issue_agent_token, list_agent_tokens,
                                      revoke_agent_token)

agent_access_bp = Blueprint("agent_access_bp", __name__)

MCP_PATH = "/mcp"


def _endpoint_url():
    """The address a client types into their AI application."""
    return request.url_root.rstrip("/") + MCP_PATH


@agent_access_bp.route("/settings/agent-access", methods=["GET", "POST"])
@login_required
def agent_access():
    issued = None

    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            label = (request.form.get("label") or "").strip() or "My agent"
            try:
                company_id = int(request.form.get("company_id") or 0)
            except ValueError:
                company_id = 0
            try:
                token, expires = issue_agent_token(current_user.id, company_id, label)
                # Handed to the template rather than flashed: a flash survives
                # into the next page, and this must be visible exactly once.
                issued = {"token": token, "expires": expires, "label": label}
            except NotPermitted as refused:
                flash(str(refused), "error")
            except Exception as exc:
                flash(f"Could not create the token: {exc}", "error")

        elif action == "revoke":
            try:
                token_id = int(request.form.get("token_id") or 0)
            except ValueError:
                token_id = 0
            if revoke_agent_token(current_user.id, token_id):
                flash("Revoked. Any agent using it has stopped working.", "success")
            else:
                flash("That token is not one of yours.", "error")
            return redirect(url_for("agent_access_bp.agent_access"))

    return render_template(
        "agent_access.html",
        tokens=list_agent_tokens(current_user.id),
        companies=companies_for(current_user.id),
        active_company_id=session.get("company_id"),
        endpoint_url=_endpoint_url(),
        issued=issued,
    )
