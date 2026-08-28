"""A small per-user rate limiter for the endpoints that cost money.

Every AI call is billed, and nothing stopped one user - or one stuck retry loop
in a browser tab - from making hundreds. This caps calls per user per window.

Counters live in PostgreSQL (see database.app_state_db) rather than in memory.
That matters more here than anywhere else: with per-process counters, running
four workers would have quietly allowed four times the configured limit, and
the thing being limited is a paid external API.
"""
import functools

from flask import jsonify, request
from flask_login import current_user


def _identity():
    try:
        if current_user.is_authenticated:
            return f"user:{current_user.id}"
    except Exception:
        pass
    return f"ip:{request.remote_addr or 'unknown'}"


def _check(bucket, limit, window):
    """(allowed, seconds_until_next_slot)."""
    from database.app_state_db import rate_limit_check

    try:
        return rate_limit_check(bucket, _identity(), limit, window)
    except Exception:
        # If the counter store is unreachable, let the request through rather
        # than locking every user out of the feature. The limiter protects
        # against runaway spend, and a database outage is already visible.
        return True, 0


def rate_limit(limit, window=60, bucket=None, message=None):
    """Allow at most `limit` calls per `window` seconds, per signed-in user.

    Applied to the AI endpoints, where each call is a paid request to an
    external provider.
    """
    def decorator(view):
        name = bucket or view.__name__

        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            allowed, retry_after = _check(name, limit, window)
            if not allowed:
                text = message or (
                    f"That is more than {limit} requests in "
                    f"{window // 60 or 1} minute(s). Please wait "
                    f"{retry_after}s and try again.")
                response = jsonify({"success": False, "message": text})
                response.status_code = 429
                response.headers["Retry-After"] = str(retry_after)
                return response
            return view(*args, **kwargs)
        return wrapper
    return decorator


def reset():
    """Clear all counters. For tests."""
    from database.app_state_db import rate_limit_clear

    try:
        rate_limit_clear()
    except Exception:
        pass
