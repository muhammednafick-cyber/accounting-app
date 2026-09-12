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
