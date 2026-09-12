"""The human gate. Nothing an agent suggests reaches the books without this.

Approving runs the ordinary posting path - the same `add_voucher` a typed
voucher goes through - so every rule applies identically: the financial year
must exist and be open, the entry must balance, the ledgers must be real. The
proposal is a suggestion about what to post, never a way around how posting
works.

Two things are deliberate. A proposal is claimed before it is posted, so a
double click or two people deciding at once cannot post it twice. And a posting
that fails leaves the proposal marked failed with the reason, rather than
quietly back in the queue where somebody would try it again expecting a
different outcome.
"""
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   session, url_for)
from flask_login import current_user, login_required

from database.agent_proposals_db import (APPROVED, FAILED, PENDING, REJECTED,
                                         claim_proposal, get_proposal,
                                         list_proposals, release_proposal,
                                         settle_proposal)

agent_proposal_bp = Blueprint("agent_proposal_bp", __name__)

REQUIRED_PERMISSION = "vouchers"


def _may_decide():
    """Approving posts a voucher, so it needs what posting one needs."""
    return current_user.is_authenticated and current_user.can_access(REQUIRED_PERMISSION)


@agent_proposal_bp.route("/settings/agent-proposals")
@login_required
def agent_proposals():
    if not _may_decide():
        flash("You need voucher access to review agent proposals.", "error")
        return redirect(url_for("dashboard_bp.dashboard"))

    company_id = session.get("company_id")
    if not company_id:
        flash("Select a company first.", "error")
        return redirect(url_for("dashboard_bp.dashboard"))

    return render_template(
        "agent_proposals.html",
        pending=list_proposals(company_id, status=PENDING),
        decided=[p for p in list_proposals(company_id, limit=40)
                 if p["status"] != PENDING][:20],
    )


@agent_proposal_bp.route("/settings/agent-proposals/<int:proposal_id>",
                         methods=["POST"])
@login_required
def decide(proposal_id):
    if not _may_decide():
        abort(403)

    company_id = session.get("company_id")
    if not company_id:
        abort(400)

    action = request.form.get("action")
    if action not in ("approve", "reject"):
        abort(400)

    # Claiming is the guard against posting the same proposal twice: whoever
    # wins the update does the work, everyone else is told it is already done.
    proposal = claim_proposal(proposal_id, company_id)
    if not proposal:
        existing = get_proposal(proposal_id, company_id)
        if existing:
            flash(f"Proposal #{proposal_id} was already {existing['status']}.",
                  "warning")
        else:
            flash("That proposal does not exist.", "error")
        return redirect(url_for("agent_proposal_bp.agent_proposals"))

    if action == "reject":
        settle_proposal(proposal_id, company_id, REJECTED, current_user.id,
                        note=(request.form.get("note") or "").strip()[:300])
        flash(f"Proposal #{proposal_id} rejected. Nothing was posted.", "success")
        return redirect(url_for("agent_proposal_bp.agent_proposals"))

    try:
        voucher_number = _post(proposal, company_id)
    except Exception as exc:
        # Marked failed rather than returned to the queue: a proposal that has
        # just been refused by the posting rules would only be refused again,
        # and leaving it pending invites somebody to keep pressing Approve.
        settle_proposal(proposal_id, company_id, FAILED, current_user.id,
                        note=str(exc)[:300])
        flash(f"Proposal #{proposal_id} could not be posted: {exc}", "error")
        return redirect(url_for("agent_proposal_bp.agent_proposals"))

    settle_proposal(proposal_id, company_id, APPROVED, current_user.id,
                    voucher_number=voucher_number)
    _record_audit(voucher_number, proposal_id, company_id)
    flash(f"Posted as {voucher_number}.", "success")
    return redirect(url_for("agent_proposal_bp.agent_proposals"))


def _post(proposal, company_id):
    """Post through the ordinary path, so every ordinary rule applies.

    The item lines travel with the voucher. Passing an empty list here is what
    put two purchases in the books that recorded the cost and received none of
    the goods; a purchase without its items is not a purchase.
    """
    from database import add_voucher
    from .models import parse_date

    payload = proposal["payload"]
    items = payload.get("item_entries") or []
    if payload["voucher_type"] == "Purchase" and not items:
        raise ValueError("This purchase carries no item lines and would record "
                         "the cost without receiving the goods.")

    return add_voucher(
        payload["voucher_type"],
        parse_date(payload["date"]),
        payload["ledger_entries"],
        items,
        None,
        narration=payload.get("narration", ""),
        original_invoice_ref=payload.get("invoice_number"),
        original_invoice_date=(parse_date(payload["invoice_date"])
                               if payload.get("invoice_date") else None),
        company_id=company_id,
    )


def _record_audit(voucher_number, proposal_id, company_id):
    """Say in the audit trail that an agent proposed this and who agreed."""
    try:
        from database.audit_db import log_audit
        log_audit(
            "AGENT_PROPOSAL_APPROVED",
            voucher_number=voucher_number,
            details=(f"Proposal #{proposal_id} suggested by an AI agent, "
                     f"approved by {current_user.username}"),
            company_id=company_id,
            username=current_user.username,
        )
    except Exception:
        # The voucher is posted; a missing audit line must not undo that, but
        # it is worth knowing about.
        from flask import current_app
        current_app.logger.warning("Could not record the approval in the audit "
                                   "trail", exc_info=True)
