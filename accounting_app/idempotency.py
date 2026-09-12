"""One posting per submission, however many times the browser sends it.

A double-click, an impatient refresh, or a proxy retrying a request that had
already reached the application all arrive as a second identical POST. Without
a claim on the submission, each one posts its own voucher, and the duplicate is
only discovered later in the ledger.

Every guarded form carries a one-time `submission_token`. The first POST to
claim that token in the database does the work and records what it produced;
any later POST with the same token replays that outcome instead of posting
again. The claim lives in the database rather than in memory so the guard still
holds when the retry lands on a different worker.

A submission that failed releases its claim: the user corrects the form and the
same token may post once more.
"""
import uuid
from functools import wraps

from flask import flash, jsonify, redirect, request, session, url_for
from werkzeug.wrappers import Response as BaseResponse

TOKEN_FIELD = "submission_token"


def new_token():
    """A fresh submission token, for a form about to be drawn."""
    return uuid.uuid4().hex


def _is_ajax():
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _identity():
    from flask_login import current_user
    return getattr(current_user, "id", None) or session.get("_id") or "anonymous"


def _status_of(rv):
    if isinstance(rv, tuple):
        for part in rv[1:]:
            if isinstance(part, int):
                return part
        return 200
    if isinstance(rv, BaseResponse):
        return rv.status_code
    return 200


def _body_of(rv):
    response = rv[0] if isinstance(rv, tuple) else rv
    if isinstance(response, BaseResponse) and response.is_json:
        try:
            body = response.get_json(silent=True)
        except Exception:
            return None
        return body if isinstance(body, dict) else None
    return None


def _outcome(rv):
    """What this response should replay as, or None when it must not be replayed."""
    if _status_of(rv) >= 400:
        return None
    body = _body_of(rv)
    if body is not None:
        if body.get("success") is False:
            return None
        return {"json": body}
    response = rv[0] if isinstance(rv, tuple) else rv
    location = getattr(response, "location", None) if isinstance(response, BaseResponse) else None
    # A redirect still has its flash in the session - nothing has rendered yet -
    # so the confirmation the user would have seen can be replayed with it.
    flashes = session.get("_flashes") or []
    message = flashes[-1][1] if flashes else None
    return {"redirect": location, "message": message}


def _replay(outcome):
    """Answer a repeat submission with what the first one produced."""
    note = "This was already submitted; it has not been posted twice."
    if outcome is None:                       # still running somewhere
        if _is_ajax():
            return jsonify(success=False, duplicate=True,
                           message="This submission is still being processed. "
                                   "Please wait rather than sending it again."), 409
        flash("That submission is still being processed. Please wait for it to "
              "finish rather than sending it again.", "warning")
        return redirect(request.referrer or url_for("dashboard_bp.dashboard"))
    if "json" in outcome:
        body = dict(outcome["json"])
        body["duplicate"] = True
        body["message"] = f"{body.get('message', 'Already submitted')} ({note})"
        return jsonify(body)
    if _is_ajax():
        return jsonify(success=True, duplicate=True,
                       message=outcome.get("message") or note)
    flash(f"{outcome.get('message') or 'Already submitted'} ({note})", "success")
    return redirect(outcome.get("redirect")
                    or request.referrer
                    or url_for("dashboard_bp.dashboard"))


def guard_duplicate_submission(fn):
    """Post at most once per submission token."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        from database.app_state_db import (submission_claim, submission_finish,
                                           submission_release)
        token = (request.form.get(TOKEN_FIELD)
                 or request.headers.get("X-Submission-Token") or "").strip()
        if not token:
            # An older page, or a caller that does not send one. Guarding is an
            # improvement, not a new requirement for posting.
            return fn(*args, **kwargs)
        try:
            state, outcome = submission_claim(token, _identity(), request.endpoint)
        except Exception:
            # The guard must never be the reason a voucher cannot be posted.
            return fn(*args, **kwargs)
        if state != "won":
            return _replay(outcome)
        try:
            rv = fn(*args, **kwargs)
        except Exception:
            try:
                submission_release(token)
            except Exception:
                pass
            raise
        recorded = _outcome(rv)
        try:
            if recorded is None:
                submission_release(token)
            else:
                submission_finish(token, recorded)
        except Exception:
            pass
        return rv
    return wrapped
