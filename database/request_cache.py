"""Bounded read memoization, isolated to a single HTTP request.

No stale financial results can survive a request, worker, company switch or
permission change. Writes clear the memo before executing.
"""
from copy import deepcopy
from functools import wraps
from flask import g, has_request_context


def clear():
    if has_request_context():
        g.report_memo = {}


def memoize(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not has_request_context():
            return fn(*args, **kwargs)
        cache = getattr(g, 'report_memo', None)
        if cache is None:
            cache = g.report_memo = {}
        key = (fn.__module__, fn.__name__, repr(args), repr(sorted(kwargs.items())))
        if key not in cache:
            result = fn(*args, **kwargs)
            if len(cache) < 32:
                cache[key] = deepcopy(result)
            return result
        return deepcopy(cache[key])
    return wrapped
