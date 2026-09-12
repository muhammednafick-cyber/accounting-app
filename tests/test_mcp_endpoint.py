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
        self.assertIn("Bearer", response.headers.get("WWW-Authenticate", ""))

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
        from accounting_app.chat_toolkit import TOOLS
        self.assertEqual(len(self._tools_for(user(admin=True))), len(TOOLS))

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
