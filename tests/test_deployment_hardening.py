"""The settings that decide whether this is safe to put on the internet.

Each of these was wrong in a way that only shows up in production: the debugger
served to the public, an upload with no ceiling, and a reverse proxy that made
every visitor look like localhost over plain HTTP.
"""
import contextlib
import io
import os
import unittest
from unittest.mock import patch

from werkzeug.middleware.proxy_fix import ProxyFix

PROXY_KEYS = ("BEHIND_HTTPS_PROXY", "TRUST_PROXY_HEADERS", "RENDER",
              "SESSION_COOKIE_SECURE", "ENABLE_HSTS", "MAX_UPLOAD_MB")


@contextlib.contextmanager
def deployment(**environment):
    """Build an app under this environment and keep it in force for requests.

    Some of these settings are read per request (the HSTS header), so patching
    only while create_app runs would prove nothing.
    """
    from accounting_app import create_app
    saved = {key: os.environ.get(key) for key in PROXY_KEYS}
    try:
        for key in PROXY_KEYS:
            os.environ.pop(key, None)
        for key, value in environment.items():
            if value is not None:
                os.environ[key] = str(value)
        with patch("accounting_app.initialize_db"):
            app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        yield app
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def probe_app(app, rule, view, methods=("GET",)):
    """Attach a bare route, past the sign-in guard these tests are not about."""
    app.before_request_funcs.clear()
    app.add_url_rule(rule, "probe", view, methods=list(methods))
    return app.test_client()


class DebugDefaultTests(unittest.TestCase):
    """Reading app.py rather than running it: __main__ never executes here."""

    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "app.py"), encoding="utf-8") as handle:
            self.source = handle.read()

    def test_debug_is_off_unless_asked_for(self):
        self.assertIn('os.environ.get("FLASK_DEBUG", "0")', self.source,
                      "debug must default to off: a deployment that forgets the "
                      "variable would otherwise serve the Werkzeug console")

    def test_a_frozen_build_never_enables_debug(self):
        self.assertIn("debug=False", self.source)


class UploadLimitTests(unittest.TestCase):
    def test_uploads_are_capped_by_default(self):
        with deployment() as app:
            self.assertEqual(app.config["MAX_CONTENT_LENGTH"], 25 * 1024 * 1024)

    def test_the_cap_is_configurable(self):
        with deployment(MAX_UPLOAD_MB="5") as app:
            self.assertEqual(app.config["MAX_CONTENT_LENGTH"], 5 * 1024 * 1024)

    def test_a_nonsense_cap_falls_back_rather_than_failing_to_boot(self):
        with deployment(MAX_UPLOAD_MB="twenty") as app:
            self.assertEqual(app.config["MAX_CONTENT_LENGTH"], 25 * 1024 * 1024)

    def test_an_oversized_upload_is_explained(self):
        with deployment(MAX_UPLOAD_MB="1") as app:
            def view():
                from flask import request
                return str(len(request.files))      # reads the body

            client = probe_app(app, "/upload-probe", view, methods=("POST",))
            payload = {"file": (io.BytesIO(b"x" * (2 * 1024 * 1024)), "big.xlsx")}
            response = client.post("/upload-probe", data=payload,
                                   content_type="multipart/form-data",
                                   headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 413)
        body = response.get_json()
        self.assertFalse(body["success"])
        self.assertIn("1 MB", body["message"])

    def test_an_upload_within_the_limit_is_untouched(self):
        with deployment(MAX_UPLOAD_MB="1") as app:
            def view():
                from flask import request
                return str(len(request.files["file"].read()))

            client = probe_app(app, "/upload-probe", view, methods=("POST",))
            payload = {"file": (io.BytesIO(b"x" * 2048), "small.xlsx")}
            response = client.post("/upload-probe", data=payload,
                                   content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.decode(), "2048")


class ReverseProxyTests(unittest.TestCase):
    def test_proxy_headers_are_ignored_when_nothing_says_there_is_a_proxy(self):
        with deployment() as app:
            self.assertNotIsInstance(
                app.wsgi_app, ProxyFix,
                "trusting these headers while exposed directly would let a "
                "client claim any address it liked")
            self.assertFalse(app.config.get("SESSION_COOKIE_SECURE", False))

            def view():
                from flask import request
                return request.remote_addr or "none"

            client = probe_app(app, "/whoami", view)
            response = client.get("/whoami", headers={"X-Forwarded-For": "203.0.113.9"})
        self.assertNotEqual(response.data.decode(), "203.0.113.9")

    def test_a_tls_proxy_turns_on_the_whole_set(self):
        with deployment(BEHIND_HTTPS_PROXY="1") as app:
            self.assertIsInstance(app.wsgi_app, ProxyFix)
            self.assertTrue(app.config["SESSION_COOKIE_SECURE"])

            client = probe_app(app, "/whoami", lambda: "ok")
            response = client.get("/whoami", headers={"X-Forwarded-Proto": "https"})
        self.assertIn("max-age=31536000",
                      response.headers.get("Strict-Transport-Security", ""))

    def test_the_client_address_and_scheme_survive_the_proxy(self):
        with deployment(BEHIND_HTTPS_PROXY="1") as app:
            def view():
                from flask import request
                return f"{request.remote_addr} {request.scheme}"

            client = probe_app(app, "/whoami", view)
            response = client.get("/whoami", headers={
                "X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"})
        self.assertEqual(response.data.decode(), "203.0.113.9 https")

    def test_trusting_proxy_headers_alone_does_not_imply_tls(self):
        # A proxy that does not terminate TLS must not get a Secure-only cookie
        # the browser would then refuse to send back.
        with deployment(TRUST_PROXY_HEADERS="1") as app:
            self.assertIsInstance(app.wsgi_app, ProxyFix)
            self.assertFalse(app.config.get("SESSION_COOKIE_SECURE", False))


if __name__ == "__main__":
    unittest.main()
