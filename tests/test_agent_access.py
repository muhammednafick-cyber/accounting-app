"""A token is a standing credential to a company's books.

So the rules about who may create one, who may see one, and who may revoke one
matter more than the screen around them. These test the rules.
"""
import unittest
from unittest.mock import patch

from database import agent_tokens_db as tokens


class IssueRulesTests(unittest.TestCase):
    """Nobody may issue themselves access the application denies them."""

    def test_a_company_the_user_cannot_open_is_refused(self):
        with patch.object(tokens, "_may_access", return_value=False):
            with self.assertRaises(tokens.NotPermitted):
                tokens.issue_agent_token(2, 99, "sneaky")

    def test_nothing_is_written_when_it_is_refused(self):
        with patch.object(tokens, "_may_access", return_value=False), \
             patch.object(tokens, "get_connection") as connection:
            with self.assertRaises(tokens.NotPermitted):
                tokens.issue_agent_token(2, 99, "sneaky")
        connection.assert_not_called()

    def test_a_permitted_company_produces_a_token(self):
        with patch.object(tokens, "_may_access", return_value=True), \
             patch.object(tokens, "get_connection") as connection:
            token, expires = tokens.issue_agent_token(1, 1, "laptop")
        self.assertGreater(len(token), 30, "a guessable token is no token")
        self.assertGreater((expires - __import__("datetime").datetime.now()).days, 300)
        connection.return_value.commit.assert_called_once()

    def test_two_tokens_are_never_the_same(self):
        with patch.object(tokens, "_may_access", return_value=True), \
             patch.object(tokens, "get_connection"):
            first, _ = tokens.issue_agent_token(1, 1, "a")
            second, _ = tokens.issue_agent_token(1, 1, "b")
        self.assertNotEqual(first, second)

    def test_the_label_is_marked_as_an_agent_token(self):
        # A phone token and an agent token live in the same table; the screen
        # and the revoke path both rely on telling them apart.
        captured = {}
        with patch.object(tokens, "_may_access", return_value=True), \
             patch.object(tokens, "get_connection") as connection:
            cursor = connection.return_value.cursor.return_value
            cursor.execute.side_effect = lambda q, p=None: captured.update(params=p)
            tokens.issue_agent_token(1, 1, "laptop")
        self.assertTrue(captured["params"][3].startswith(tokens.AGENT_PREFIX))
        self.assertIn("laptop", captured["params"][3])

    def test_an_overlong_label_cannot_overflow_the_column(self):
        captured = {}
        with patch.object(tokens, "_may_access", return_value=True), \
             patch.object(tokens, "get_connection") as connection:
            cursor = connection.return_value.cursor.return_value
            cursor.execute.side_effect = lambda q, p=None: captured.update(params=p)
            tokens.issue_agent_token(1, 1, "x" * 500)
        self.assertLessEqual(len(captured["params"][3]), 120)


class RevokeRulesTests(unittest.TestCase):
    """A token id from someone else's screen must not be revocable by guessing."""

    def test_revoking_is_scoped_to_the_owner(self):
        captured = {}
        with patch.object(tokens, "get_connection") as connection:
            cursor = connection.return_value.cursor.return_value
            cursor.execute.side_effect = lambda q, p=None: captured.update(
                query=q, params=p)
            cursor.rowcount = 1
            tokens.revoke_agent_token(user_id=7, token_id=3)
        self.assertIn("user_id = %s", captured["query"])
        self.assertEqual(captured["params"][0], 3)
        self.assertEqual(captured["params"][1], 7)

    def test_revoking_somebody_elses_token_changes_nothing(self):
        with patch.object(tokens, "get_connection") as connection:
            connection.return_value.cursor.return_value.rowcount = 0
            self.assertFalse(tokens.revoke_agent_token(user_id=7, token_id=3))

    def test_a_phone_token_cannot_be_revoked_from_this_screen(self):
        captured = {}
        with patch.object(tokens, "get_connection") as connection:
            cursor = connection.return_value.cursor.return_value
            cursor.execute.side_effect = lambda q, p=None: captured.update(
                query=q, params=p)
            cursor.rowcount = 0
            tokens.revoke_agent_token(7, 3)
        self.assertIn("device LIKE", captured["query"])


class ListingRulesTests(unittest.TestCase):
    def test_the_value_is_never_listed_in_full(self):
        row = (3, "abcdefghijklmnopqrstuvwxyz0123456789", "MCP · laptop",
               "Nafi", None, None, None)
        with patch.object(tokens, "get_connection") as connection:
            connection.return_value.cursor.return_value.fetchall.return_value = [row]
            listed = tokens.list_agent_tokens(7)
        self.assertEqual(listed[0]["prefix"], "abcdefgh")
        self.assertNotIn("token", listed[0],
                         "the full value must never reach a page again")
        self.assertEqual(listed[0]["label"], "laptop")

    def test_only_this_users_tokens_are_asked_for(self):
        captured = {}
        with patch.object(tokens, "get_connection") as connection:
            cursor = connection.return_value.cursor.return_value
            cursor.execute.side_effect = lambda q, p=None: captured.update(
                query=q, params=p)
            cursor.fetchall.return_value = []
            tokens.list_agent_tokens(7)
        self.assertIn("t.user_id = %s", captured["query"])
        self.assertEqual(captured["params"][0], 7)


class ScreenTests(unittest.TestCase):
    def setUp(self):
        from accounting_app import create_app
        with patch("accounting_app.initialize_db"):
            self.app = create_app()
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.client = self.app.test_client()

    def test_the_page_needs_a_sign_in(self):
        response = self.client.get("/settings/agent-access")
        self.assertEqual(response.status_code, 302)
        self.assertIn("signin", response.headers["Location"])

    def test_the_token_is_shown_once_and_not_flashed(self):
        # A flash survives into the next page; the value must appear exactly
        # once, on the response that created it.
        import accounting_app.agent_access_routes as routes

        with patch.object(routes, "issue_agent_token") as issue, \
             patch.object(routes, "list_agent_tokens", return_value=[]), \
             patch.object(routes, "companies_for", return_value=[]), \
             patch.object(routes, "flash") as flashed:
            from datetime import datetime
            issue.return_value = ("secret-token-value", datetime(2027, 9, 12))
            with self.app.test_request_context():
                pass
            self._sign_in()
            response = self.client.post("/settings/agent-access", data={
                "action": "create", "label": "laptop", "company_id": "1"})
        self.assertIn(b"secret-token-value", response.data)
        flashed.assert_not_called()

    def test_a_refused_company_tells_the_user_why(self):
        import accounting_app.agent_access_routes as routes

        with patch.object(routes, "issue_agent_token",
                          side_effect=tokens.NotPermitted("No access.")), \
             patch.object(routes, "list_agent_tokens", return_value=[]), \
             patch.object(routes, "companies_for", return_value=[]), \
             patch.object(routes, "flash") as flashed:
            self._sign_in()
            response = self.client.post("/settings/agent-access", data={
                "action": "create", "label": "x", "company_id": "99"})
        self.assertEqual(response.status_code, 200)
        flashed.assert_called_once()
        self.assertIn("No access.", flashed.call_args[0][0])

    def _sign_in(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = "1"
            session["_fresh"] = True
            session["company_id"] = 1


if __name__ == "__main__":
    unittest.main()


class NavigationTests(unittest.TestCase):
    """A page nobody can find is a page that does not exist."""

    def setUp(self):
        from accounting_app import create_app
        with patch("accounting_app.initialize_db"):
            self.app = create_app()
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    def test_the_sidebar_links_to_agent_access(self):
        import io, os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "templates", "base.html"),
                     encoding="utf-8") as handle:
            base = handle.read()
        self.assertIn("agent_access_bp.agent_access", base,
                      "the page is only reachable by typing its URL")

    def test_the_link_is_not_hidden_behind_a_permission(self):
        # A token carries only its owner's access, so managing one is not a
        # privilege. A user without 'setup' must still be able to reach it.
        import io, os, re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "templates", "base.html"),
                     encoding="utf-8") as handle:
            base = handle.read()
        line = next(l for l in base.splitlines()
                    if "agent_access_bp.agent_access" in l)
        index = base.index(line)
        # Walk back to the nearest open conditional and make sure the link is
        # not inside a can_access(...) or is_admin block.
        preceding = base[:index]
        last_if = preceding.rfind("{% if")
        last_endif = preceding.rfind("{% endif %}")
        self.assertGreater(last_endif, last_if,
                           "the Agent Access link sits inside a conditional block")


class ResponsiveLayoutTests(unittest.TestCase):
    """Both screens are used from a phone, not only a desk."""

    def _page(self, name):
        import io, os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "templates", name), encoding="utf-8") as f:
            return f.read()

    def test_every_table_can_scroll_inside_its_own_box(self):
        # Otherwise the whole page scrolls sideways and the buttons drift off
        # the edge of a phone screen.
        for name in ("agent_access.html", "agent_proposals.html"):
            page = self._page(name)
            self.assertEqual(page.count('<div class="table-scroll">'),
                             page.count('<table'),
                             name + ': every table needs its own scroll box')

    def test_the_layout_stacks_below_tablet_width(self):
        for name in ("agent_access.html", "agent_proposals.html"):
            page = self._page(name)
            self.assertIn("@media (max-width: 820px)", page, name)

    def test_controls_are_thumb_sized_on_a_phone(self):
        for name in ("agent_access.html", "agent_proposals.html"):
            self.assertIn("min-height: 44px", self._page(name), name)

    def test_the_token_is_not_squeezed_against_its_button(self):
        page = self._page("agent_access.html")
        self.assertIn(".token-value { flex-direction: column", page)
