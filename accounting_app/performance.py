"""Request timings without logging query parameters or customer data."""
import hashlib
import logging
import time
from flask import g, has_request_context, request

log = logging.getLogger('accounting.performance')


def record_query(query, elapsed):
    if has_request_context():
        g.query_count = getattr(g, 'query_count', 0) + 1
        g.query_seconds = getattr(g, 'query_seconds', 0.0) + elapsed
    if elapsed >= 0.5:
        fingerprint = hashlib.sha256(' '.join(query.split()).encode()).hexdigest()[:16]
        log.warning('slow_query fingerprint=%s duration_ms=%.1f', fingerprint, elapsed * 1000)


def init_app(app):
    @app.before_request
    def start():
        g.performance_started = time.perf_counter()

    @app.after_request
    def finish(response):
        duration = time.perf_counter() - getattr(g, 'performance_started', time.perf_counter())
        response.headers['Server-Timing'] = 'app;dur=%.1f, db;dur=%.1f' % (
            duration * 1000, getattr(g, 'query_seconds', 0) * 1000)
        if duration >= 1:
            log.warning('slow_request endpoint=%s status=%s duration_ms=%.1f queries=%s',
                        request.endpoint, response.status_code, duration * 1000,
                        getattr(g, 'query_count', 0))
        return response
