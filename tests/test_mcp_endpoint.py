"""The MCP endpoint is the first way into the books that is not a browser.

Everything here is about boundaries rather than features: an agent must see
only what the person behind its token could open by hand, in one company, and
must get nothing at all without a valid token.
"""
import json
import os
import unittest
from unittest.mock import patch

from accounting_app import mcp_routes
from accounting_app.models import User


def build_app():
    from accounting_app import create_app
    with patch("accounting_app.initialize_db"):
        app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return app


def user(*permissions, admin=False):
    return User(7, "agent-user", "a@example.com", "x", int(admin), 0,
                set(permissions))


def rpc(client, method, params=None, token="good-token", request_id=1):
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/mcp", data=json.dumps(body), headers=headers)


class AuthenticationTests(unittest.TestCase):
    """No token, no books."""

    def setUp(self):
        self.app = build_app()
        self.client = self.app.test_client()

    def test_a_call_without_a_token_is_refused(self):
        with patch.object(mcp_routes, "_caller", return_value=None):
            response = rpc(self.client, "tools/list", token=None)
        self.assertEqual(response.status_code, 401)

    def test_the_refusal_does_not_advertise_an_oauth_flow(self):
        # A client reads any Bearer challenge as "this server has OAuth", goes
        # looking for a discovery document, and never falls back to the token
        # the operator configured by hand.
        with patch.object(mcp_routes, "_caller", return_value=None):
            response = rpc(self.client, "tools/list", token=None)
        self.assertIsNone(response.headers.get("WWW-Authenticate"))

    def test_an_unknown_token_is_refused(self):
        with patch.object(mcp_routes, "_caller", return_value=None):
            response = rpc(self.client, "tools/list", token="made-up")
        self.assertEqual(response.status_code, 401)

    def test_the_refusal_leaks_nothing_about_the_company(self):
        with patch.object(mcp_routes, "_caller", return_value=None):
            response = rpc(self.client, "tools/list", token=None)
        body = response.get_data(as_text=True).lower()
        for leak in ("ledger", "voucher", "company_id", "traceback"):
            self.assertNotIn(leak, body)


class HandshakeTests(unittest.TestCase):
    """Both the current handshake and the older one must be answered."""

    def setUp(self):
        self.app = build_app()
        self.client = self.app.test_client()
        self.caller = patch.object(mcp_routes, "_caller", return_value=(7, 1))
        self.caller.start()
        self.addCleanup(self.caller.stop)
        self.limit = patch.object(mcp_routes, "rate_limit_check",
                                  return_value=(True, 0))
        self.limit.start()
        self.addCleanup(self.limit.stop)

    def test_server_discover_reports_versions_and_identity(self):
        response = rpc(self.client, "server/discover")
        result = response.get_json()["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertIn("2026-07-28", result["supportedVersions"])
        self.assertIn("tools", result["capabilities"])
        info = result["_meta"]["io.modelcontextprotocol/serverInfo"]
        self.assertEqual(info["name"], "prodata-accounts")

    def test_the_older_initialize_handshake_still_works(self):
        response = rpc(self.client, "initialize",
                       {"protocolVersion": "2025-06-18"})
        result = response.get_json()["result"]
        # Answer in the version the client asked for, not ours.
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertEqual(result["serverInfo"]["name"], "prodata-accounts")

    def test_an_unknown_requested_version_falls_back_to_ours(self):
        response = rpc(self.client, "initialize",
                       {"protocolVersion": "1999-01-01"})
        self.assertEqual(response.get_json()["result"]["protocolVersion"],
                         "2026-07-28")

    def test_a_notification_gets_no_reply(self):
        body = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        response = self.client.post("/mcp", data=json.dumps(body), headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer good-token"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_data(), b"")

    def test_an_unknown_method_is_a_jsonrpc_error(self):
        response = rpc(self.client, "resources/list")
        self.assertEqual(response.get_json()["error"]["code"], -32601)

    def test_ping_is_answered(self):
        self.assertEqual(rpc(self.client, "ping").get_json()["result"], {})


class CatalogueTests(unittest.TestCase):
    """An agent must not even see a tool it may not run."""

    def setUp(self):
        self.app = build_app()
        self.client = self.app.test_client()
        for target, value in (("_caller", (7, 1)),):
            p = patch.object(mcp_routes, target, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(mcp_routes, "rate_limit_check", return_value=(True, 0))
        p.start()
        self.addCleanup(p.stop)

    def _tools_for(self, u):
        from accounting_app import chat_permissions as permissions
        with patch.object(permissions, "_user", return_value=u):
            return rpc(self.client, "tools/list").get_json()["result"]["tools"]

    def test_an_administrator_sees_every_tool(self):
        # Every read-only question, plus the one tool that suggests a voucher.
        from accounting_app.chat_toolkit import TOOLS
        names = {t[chr(34)+'name'+chr(34)] if False else t['name']
                 for t in self._tools_for(user(admin=True))}
        self.assertEqual(len(names), len(TOOLS) + 1)
        self.assertIn('propose_voucher', names)
        self.assertTrue(set(TOOLS) <= names)

    def test_a_reports_only_user_does_not_see_user_management(self):
        names = {t["name"] for t in self._tools_for(user("reports"))}
        self.assertNotIn("list_users", names)
        self.assertTrue(names, "a reports user should still see some tools")

    def test_a_user_with_no_permissions_sees_nothing(self):
        self.assertEqual(self._tools_for(user()), [])

    def test_every_tool_carries_a_usable_schema(self):
        for tool in self._tools_for(user(admin=True)):
            self.assertTrue(tool["name"])
            self.assertTrue(tool["description"], f"{tool['name']} has no description")
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertIsInstance(schema["properties"], dict)
            for name in schema["required"]:
                self.assertIn(name, schema["properties"],
                              f"{tool['name']} requires an undeclared property")


class ToolCallTests(unittest.TestCase):
    def setUp(self):
        self.app = build_app()
        self.client = self.app.test_client()
        p = patch.object(mcp_routes, "_caller", return_value=(7, 1))
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(mcp_routes, "rate_limit_check", return_value=(True, 0))
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(mcp_routes, "_log_call")
        p.start()
        self.addCleanup(p.stop)

    def test_the_company_comes_from_the_token_not_the_request(self):
        # An agent must not be able to ask for another company by passing one.
        captured = {}

        def fake_run(name, args, company_id=None, state=None):
            captured["company_id"] = company_id
            captured["args"] = args
            return {"title": "ok", "columns": ["a"], "rows": [[1]]}, {}

        with patch("accounting_app.chat_toolkit.run", fake_run):
            rpc(self.client, "tools/call",
                {"name": "list_ledgers", "arguments": {"company_id": "999"}})
        self.assertEqual(captured["company_id"], 1)

    def test_a_denied_tool_answers_in_content_not_as_a_transport_error(self):
        from accounting_app.chat_permissions import PermissionDenied

        with patch("accounting_app.chat_toolkit.run",
                   side_effect=PermissionDenied("list_users", "setup.users")):
            body = rpc(self.client, "tools/call",
                       {"name": "list_users", "arguments": {}}).get_json()
        self.assertNotIn("error", body)
        self.assertTrue(body["result"]["isError"])
        self.assertIn("permission", body["result"]["content"][0]["text"].lower())

    def test_an_unknown_tool_is_rejected(self):
        body = rpc(self.client, "tools/call",
                   {"name": "drop_everything", "arguments": {}}).get_json()
        self.assertEqual(body["error"]["code"], -32602)

    def test_a_failing_tool_does_not_leak_a_traceback(self):
        with patch("accounting_app.chat_toolkit.run",
                   side_effect=RuntimeError("relation \"secret\" does not exist")):
            body = rpc(self.client, "tools/call",
                       {"name": "list_ledgers", "arguments": {}}).get_json()
        self.assertTrue(body["result"]["isError"])
        self.assertNotIn("Traceback", body["result"]["content"][0]["text"])

    def test_a_table_is_rendered_as_readable_text(self):
        result = {"title": "Trial Balance", "columns": ["Ledger", "Debit"],
                  "rows": [["Cash", "1,000.00"]], "totals": ["", "1,000.00"]}
        with patch("accounting_app.chat_toolkit.run", return_value=(result, {})):
            body = rpc(self.client, "tools/call",
                       {"name": "trial_balance", "arguments": {}}).get_json()
        text = body["result"]["content"][0]["text"]
        self.assertIn("Trial Balance", text)
        self.assertIn("Cash", text)
        self.assertIn("Totals", text)

    def test_an_empty_result_still_says_something(self):
        with patch("accounting_app.chat_toolkit.run",
                   return_value=({"title": "Nothing"}, {})):
            body = rpc(self.client, "tools/call",
                       {"name": "list_ledgers", "arguments": {}}).get_json()
        self.assertTrue(body["result"]["content"][0]["text"].strip())


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        self.app = build_app()
        self.client = self.app.test_client()
        p = patch.object(mcp_routes, "_caller", return_value=(7, 1))
        p.start()
        self.addCleanup(p.stop)

    def test_a_throttled_caller_is_told_when_to_retry(self):
        with patch.object(mcp_routes, "rate_limit_check", return_value=(False, 42)):
            response = rpc(self.client, "tools/list")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "42")


class WriteSurfaceTests(unittest.TestCase):
    """The safety property the whole design rests on."""

    def test_the_toolkit_contains_no_write_statements(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "accounting_app", "chat_toolkit.py"),
                  encoding="utf-8") as handle:
            source = handle.read().upper()
        for statement in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ", "ALTER "):
            self.assertNotIn(statement, source,
                             f"chat_toolkit gained a {statement.strip()} statement - "
                             "the MCP endpoint is no longer read-only")


if __name__ == "__main__":
    unittest.main()


class RenderingTests(unittest.TestCase):
    """What the model reads has to be clean prose, not chat-window markup."""

    def test_bold_tags_are_stripped(self):
        # The toolkit writes summaries for the in-app chat window, where <b>
        # renders. A model would repeat the tags verbatim at the user.
        out = mcp_routes._render({
            "title": "Trial Balance",
            "summary": "total debit <b>618,603.47</b>, total credit <b>618,603.47</b>",
        })
        text = out["content"][0]["text"]
        self.assertNotIn("<b>", text)
        self.assertIn("618,603.47", text)

    def test_entities_are_decoded(self):
        out = mcp_routes._render({"summary": "Smith &amp; Sons owe 40"})
        self.assertIn("Smith & Sons", out["content"][0]["text"])

    def test_totals_render_their_figures_not_their_labels(self):
        # totals is a mapping, not a row. Iterating it as a row printed
        # "Total Debit | Total Credit" and silently dropped every number.
        out = mcp_routes._render({
            "title": "Trial Balance",
            "columns": ["Ledger"], "rows": [["Cash"]],
            "totals": {"Total Debit": "618,603.47", "Total Credit": "618,603.47"},
        })
        text = out["content"][0]["text"]
        self.assertIn("Total Debit: 618,603.47", text)
        self.assertIn("Total Credit: 618,603.47", text)

    def test_a_totals_list_still_renders(self):
        out = mcp_routes._render({"totals": ["1,000.00", "2,000.00"]})
        self.assertIn("1,000.00", out["content"][0]["text"])

    def test_rows_are_rendered_under_their_columns(self):
        out = mcp_routes._render({
            "columns": ["Ledger", "Balance"],
            "rows": [["Nafi-Cash", "55394.53"], ["Bank", None], ["Petty", "12.00"]],
        })
        text = out["content"][0]["text"]
        self.assertIn("Ledger | Balance", text)
        self.assertIn("Nafi-Cash | 55394.53", text)
        # A null cell renders blank rather than the word "None".
        self.assertIn("Bank |", text)
        self.assertNotIn("None", text)


class AlternativeHeaderTests(unittest.TestCase):
    """Some connector interfaces reserve Authorization for their own sign-in."""

    def setUp(self):
        self.app = build_app()
        self.client = self.app.test_client()

    def test_a_bearer_token_is_still_the_normal_way(self):
        with patch.object(mcp_routes, "_identify_token") as fallback, \
             patch("accounting_app.mobile_api._identify", return_value=(7, 1)), \
             patch.object(mcp_routes, "rate_limit_check", return_value=(True, 0)):
            response = rpc(self.client, "ping")
        self.assertEqual(response.status_code, 200)
        fallback.assert_not_called()

    def test_an_api_key_header_is_accepted_when_there_is_no_bearer(self):
        with patch("accounting_app.mobile_api._identify", return_value=None), \
             patch.object(mcp_routes, "_identify_token", return_value=(7, 1)) as look_up, \
             patch.object(mcp_routes, "rate_limit_check", return_value=(True, 0)):
            response = self.client.post("/mcp", data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "ping"}), headers={
                "Content-Type": "application/json", "X-API-Key": "the-token"})
        self.assertEqual(response.status_code, 200)
        look_up.assert_called_once_with("the-token")

    def test_an_empty_api_key_is_not_a_way_in(self):
        with patch("accounting_app.mobile_api._identify", return_value=None), \
             patch.object(mcp_routes, "_identify_token") as look_up:
            response = self.client.post("/mcp", data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "ping"}), headers={
                "Content-Type": "application/json", "X-API-Key": "   "})
        self.assertEqual(response.status_code, 401)
        look_up.assert_not_called()

    def test_a_wrong_api_key_is_refused(self):
        with patch("accounting_app.mobile_api._identify", return_value=None), \
             patch.object(mcp_routes, "_identify_token", return_value=None):
            response = self.client.post("/mcp", data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "ping"}), headers={
                "Content-Type": "application/json", "X-API-Key": "wrong"})
        self.assertEqual(response.status_code, 401)


class TransportComplianceTests(unittest.TestCase):
    """What the Streamable HTTP transport requires of a server."""

    def setUp(self):
        self.app = build_app()
        self.client = self.app.test_client()

    def test_get_is_method_not_allowed(self):
        # The standalone SSE stream was removed in this revision; the spec asks
        # a server that only speaks it to answer GET with 405. It used to
        # redirect to the sign-in page, which no client could make sense of.
        response = self.client.get("/mcp")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["Allow"], "POST")

    def test_delete_is_method_not_allowed(self):
        response = self.client.delete("/mcp")
        self.assertEqual(response.status_code, 405)

    def test_an_unknown_method_is_a_404_carrying_the_jsonrpc_error(self):
        # The status is how a client tells this server apart from one that does
        # not host the path at all.
        with patch.object(mcp_routes, "_caller", return_value=(7, 1)), \
             patch.object(mcp_routes, "rate_limit_check", return_value=(True, 0)):
            response = rpc(self.client, "resources/read")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"]["code"], -32601)

    def test_a_known_method_is_not_a_404(self):
        with patch.object(mcp_routes, "_caller", return_value=(7, 1)), \
             patch.object(mcp_routes, "rate_limit_check", return_value=(True, 0)):
            self.assertEqual(rpc(self.client, "ping").status_code, 200)

    def test_a_foreign_origin_is_refused(self):
        # A browser on another site must not be able to drive this endpoint.
        with patch.object(mcp_routes, "_caller", return_value=(7, 1)), \
             patch.object(mcp_routes, "rate_limit_check", return_value=(True, 0)):
            response = self.client.post("/mcp", data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "ping"}), headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer good-token",
                "Origin": "https://evil.example.com"})
        self.assertEqual(response.status_code, 403)

    def test_no_origin_at_all_is_fine(self):
        # An agent connects server to server and sends no Origin.
        with patch.object(mcp_routes, "_caller", return_value=(7, 1)), \
             patch.object(mcp_routes, "rate_limit_check", return_value=(True, 0)):
            self.assertEqual(rpc(self.client, "ping").status_code, 200)

    def test_our_own_origin_is_fine(self):
        with patch.object(mcp_routes, "_caller", return_value=(7, 1)), \
             patch.object(mcp_routes, "rate_limit_check", return_value=(True, 0)):
            response = self.client.post("/mcp", data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "ping"}), headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer good-token",
                "Origin": "http://localhost"})
        self.assertEqual(response.status_code, 200)


class OAuthProbeTests(unittest.TestCase):
    """A client that probes for OAuth must get a plain no, not a login page."""

    def setUp(self):
        self.app = build_app()
        self.client = self.app.test_client()

    def test_the_protected_resource_document_is_a_clean_404(self):
        for path in ("/.well-known/oauth-protected-resource",
                     "/.well-known/oauth-protected-resource/mcp",
                     "/.well-known/oauth-authorization-server"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404, path)
            self.assertNotIn("signin", response.headers.get("Location", ""),
                             f"{path} redirected instead of refusing")

    def test_the_404_explains_what_to_do_instead(self):
        body = self.client.get(
            "/.well-known/oauth-protected-resource/mcp").get_json()
        self.assertIn("without sign-in", body["message"])
        self.assertIn("X-API-Key", body["message"])


class RefusalLoggingTests(unittest.TestCase):
    """Knowing what a refused caller sent must not mean logging a credential."""

    def setUp(self):
        self.app = build_app()
        self.client = self.app.test_client()

    def _refuse_with(self, headers):
        with patch.object(mcp_routes, "_caller", return_value=None), \
             patch.object(self.app.logger, "warning") as logged:
            self.client.post("/mcp", data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "ping"}),
                headers={"Content-Type": "application/json", **headers})
        return logged.call_args[0] if logged.call_args else ()

    def test_the_token_value_is_never_written_to_the_log(self):
        args = self._refuse_with({"Authorization": "Bearer super-secret-value"})
        written = " ".join(str(a) for a in args)
        self.assertNotIn("super-secret-value", written)

    def test_the_scheme_and_length_are_recorded(self):
        args = self._refuse_with({"Authorization": "Bearer super-secret-value"})
        written = " ".join(str(a) for a in args)
        self.assertIn("bearer", written)

    def test_an_api_key_value_is_never_written_either(self):
        args = self._refuse_with({"X-API-Key": "another-secret"})
        written = " ".join(str(a) for a in args)
        self.assertNotIn("another-secret", written)
        self.assertIn("X-Api-Key", written.replace("x-api-key", "X-Api-Key"))

    def test_a_caller_sending_nothing_is_distinguishable(self):
        args = self._refuse_with({})
        written = " ".join(str(a) for a in args)
        self.assertNotIn("bearer", written)


class BareTokenTests(unittest.TestCase):
    """A connector that asks for a header value often sends the token alone."""

    def setUp(self):
        self.app = build_app()
        self.client = self.app.test_client()

    def _post(self, headers):
        with patch.object(mcp_routes, "rate_limit_check", return_value=(True, 0)):
            return self.client.post("/mcp", data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "ping"}),
                headers={"Content-Type": "application/json", **headers})

    def test_a_token_without_the_bearer_prefix_is_accepted(self):
        with patch("accounting_app.mobile_api._identify", return_value=None), \
             patch.object(mcp_routes, "_identify_token",
                          return_value=(7, 1)) as look_up:
            response = self._post({"Authorization": "rawtokenvalue123"})
        self.assertEqual(response.status_code, 200)
        look_up.assert_called_once_with("rawtokenvalue123")

    def test_a_proper_bearer_token_is_still_preferred(self):
        with patch("accounting_app.mobile_api._identify", return_value=(7, 1)), \
             patch.object(mcp_routes, "_identify_token") as look_up:
            response = self._post({"Authorization": "Bearer proper-token"})
        self.assertEqual(response.status_code, 200)
        look_up.assert_not_called()

    def test_a_wrong_bare_token_is_still_refused(self):
        with patch("accounting_app.mobile_api._identify", return_value=None), \
             patch.object(mcp_routes, "_identify_token", return_value=None):
            self.assertEqual(self._post({"Authorization": "wrong"}).status_code, 401)

    def test_a_malformed_scheme_is_not_treated_as_a_token(self):
        # "Basic abc" has a space, so it is a scheme we do not accept - not a
        # bare token that happens to contain one.
        with patch("accounting_app.mobile_api._identify", return_value=None), \
             patch.object(mcp_routes, "_identify_token") as look_up:
            self._post({"Authorization": "Basic abc123"})
        look_up.assert_not_called()


class RefusalLogSafetyTests(unittest.TestCase):
    """The diagnostic that found the bug had a bug: it printed the credential."""

    def setUp(self):
        self.app = build_app()
        self.client = self.app.test_client()

    def _logged_for(self, header_value):
        with patch.object(mcp_routes, "_caller", return_value=None), \
             patch.object(self.app.logger, "warning") as logged:
            self.client.post("/mcp", data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "ping"}),
                headers={"Content-Type": "application/json",
                         "Authorization": header_value})
        return " ".join(str(a) for a in (logged.call_args[0] if logged.call_args else ()))

    def test_a_bare_token_is_never_printed(self):
        # Splitting on a space to name the scheme printed the whole credential
        # when there was no space - the very case being diagnosed.
        written = self._logged_for("gvJDfG1Tcgm7gqORixgeJ7SoygFi1G97z3oHG3bR4")
        self.assertNotIn("gvJDfG1Tcg", written)
        self.assertIn("no recognised scheme", written)

    def test_a_bearer_token_is_never_printed(self):
        written = self._logged_for("Bearer supersecretvalue")
        self.assertNotIn("supersecretvalue", written)
        self.assertIn("bearer", written)

    def test_the_length_is_still_reported(self):
        written = self._logged_for("Bearer abc")
        self.assertIn("10 chars", written)


class ProposeToolTests(unittest.TestCase):
    """The only tool that is not a question, and the permission around it."""

    def setUp(self):
        self.app = build_app()
        self.client = self.app.test_client()
        for target, value in (("_caller", (7, 1)),):
            p = patch.object(mcp_routes, target, return_value=value)
            p.start(); self.addCleanup(p.stop)
        p = patch.object(mcp_routes, "rate_limit_check", return_value=(True, 0))
        p.start(); self.addCleanup(p.stop)
        p = patch.object(mcp_routes, "_log_call")
        p.start(); self.addCleanup(p.stop)

    def _tools_for(self, u):
        from accounting_app import chat_permissions as permissions
        with patch.object(permissions, "_user", return_value=u):
            return {t["name"] for t in
                    rpc(self.client, "tools/list").get_json()["result"]["tools"]}

    def test_a_voucher_user_is_offered_the_propose_tool(self):
        self.assertIn("propose_voucher", self._tools_for(user("vouchers")))

    def test_a_reports_only_user_is_not_offered_it(self):
        # An agent must not become a way to post for someone who cannot.
        self.assertNotIn("propose_voucher", self._tools_for(user("reports")))

    def test_a_user_with_nothing_is_not_offered_it(self):
        self.assertNotIn("propose_voucher", self._tools_for(user()))

    def test_calling_it_without_the_permission_is_refused(self):
        from accounting_app import chat_permissions as permissions
        with patch.object(permissions, "_user", return_value=user("reports")):
            body = rpc(self.client, "tools/call", {
                "name": "propose_voucher", "arguments": {}}).get_json()
        self.assertTrue(body["result"]["isError"])
        self.assertIn("permission", body["result"]["content"][0]["text"].lower())

    def test_a_valid_proposal_says_nothing_was_posted(self):
        from accounting_app import chat_permissions as permissions
        with patch.object(permissions, "_user", return_value=user("vouchers")), \
             patch("accounting_app.agent_proposals.propose_voucher",
                   return_value=(42, "Payment dated 2026-03-01, 100.00")):
            body = rpc(self.client, "tools/call", {
                "name": "propose_voucher",
                "arguments": {"voucher_type": "Payment", "date": "2026-03-01",
                              "ledger_entries": []}}).get_json()
        text = body["result"]["content"][0]["text"]
        self.assertIn("#42", text)
        self.assertIn("Nothing has been posted", text)
        self.assertNotIn("isError", str(body["result"].get("isError", "")))

    def test_a_malformed_proposal_explains_itself_to_the_agent(self):
        from accounting_app import chat_permissions as permissions
        from accounting_app.agent_proposals import ProposalRejected
        with patch.object(permissions, "_user", return_value=user("vouchers")), \
             patch("accounting_app.agent_proposals.propose_voucher",
                   side_effect=ProposalRejected("The entry does not balance.")):
            body = rpc(self.client, "tools/call", {
                "name": "propose_voucher", "arguments": {}}).get_json()
        self.assertTrue(body["result"]["isError"])
        self.assertIn("does not balance", body["result"]["content"][0]["text"])


class CapabilityHonestyTests(unittest.TestCase):
    def setUp(self):
        self.app = build_app()
        self.client = self.app.test_client()
        for t, v in (("_caller", (7, 1)),):
            p = patch.object(mcp_routes, t, return_value=v); p.start(); self.addCleanup(p.stop)
        p = patch.object(mcp_routes, "rate_limit_check", return_value=(True, 0))
        p.start(); self.addCleanup(p.stop)

    def test_the_tool_list_is_not_declared_frozen(self):
        # Declaring listChanged false told clients to cache the list for good,
        # so a tool added later never appeared. The list is filtered per token
        # and grows on deployment; it was never frozen.
        caps = rpc(self.client, "initialize",
                   {"protocolVersion": "2025-11-25"}).get_json()["result"]["capabilities"]
        self.assertNotIn("listChanged", caps.get("tools", {}))

    def test_discover_agrees(self):
        caps = rpc(self.client, "server/discover").get_json()["result"]["capabilities"]
        self.assertNotIn("listChanged", caps.get("tools", {}))
