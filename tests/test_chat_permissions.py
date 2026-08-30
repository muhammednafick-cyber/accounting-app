"""The chatbot must not be a way around the app's menu permissions.

`_user()` is patched rather than a real login: these tests are about the
mapping and the refusals, not about Flask-Login.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from accounting_app import chat_permissions as P
from accounting_app import chat_router as CR
from accounting_app import chat_toolkit as TK
from accounting_app.models import PERMISSION_KEYS, User


def user(*permissions, admin=False, principal=False):
    return User(1, "tester", "t@example.com", "x", int(admin),
                int(principal), set(permissions))


class FakeUser:
    """A user context installed for the duration of a test."""

    def __init__(self, test, u):
        self.test, self.u = test, u

    def __enter__(self):
        self.original = P._user
        P._user = lambda: self.u
        return self.u

    def __exit__(self, *exc):
        P._user = self.original


class TestMapping(unittest.TestCase):

    def test_every_tool_has_a_permission(self):
        for name in TK.TOOLS:
            self.assertIsNotNone(P.permission_for(name), name)

    def test_permissions_are_real_menu_keys(self):
        keys = set(P.TOOL_PERMISSIONS.values()) | set(P.GROUP_PERMISSIONS.values())
        keys.add(P.AI_SQL_PERMISSION)
        for key in keys:
            self.assertIn(key, PERMISSION_KEYS, key)

    def test_sensitive_tools_sit_behind_setup(self):
        self.assertEqual(P.permission_for("list_users"), "setup.user_management")
        self.assertEqual(P.permission_for("company_settings"), "setup.company_settings")


class TestChecks(unittest.TestCase):

    def test_admin_may_run_everything(self):
        with FakeUser(self, user(admin=True)):
            self.assertEqual(len(P.allowed_tool_names()), len(TK.TOOLS))

    def test_principal_may_run_everything(self):
        with FakeUser(self, user(principal=True)):
            self.assertTrue(P.can_use("list_users"))

    def test_a_sales_user_cannot_read_the_user_list(self):
        with FakeUser(self, user("reports")):
            self.assertTrue(P.can_use("sales_by_customer"))
            self.assertFalse(P.can_use("list_users"))
            self.assertFalse(P.can_use("voucher_details"))

    def test_parent_grant_covers_its_children(self):
        with FakeUser(self, user("reports")):
            self.assertTrue(P.can_use("trial_balance"))     # reports.financial_statements
            self.assertTrue(P.can_use("vat_summary"))       # reports.vat_reports

    def test_child_grant_does_not_leak_to_a_sibling(self):
        with FakeUser(self, user("reports.inventory_reports")):
            self.assertTrue(P.can_use("item_stock"))
            self.assertFalse(P.can_use("trial_balance"))

    def test_ai_sql_needs_the_reporting_permission(self):
        with FakeUser(self, user("vouchers")):
            self.assertFalse(P.can_use_ai_sql())
        with FakeUser(self, user("reports.registers")):
            self.assertTrue(P.can_use_ai_sql())

    def test_no_identified_user_is_allowed(self):
        with FakeUser(self, None):
            self.assertTrue(P.can_use("list_users"))


class TestRefusals(unittest.TestCase):

    def test_execute_refuses_before_touching_the_database(self):
        with FakeUser(self, user("reports")):
            reply = CR.execute("list_users", {}, "list users", company_id=1)
        self.assertEqual(reply["intent"], "permission_denied")
        self.assertIn("User Management", reply["response"])

    def test_catalogue_hides_what_the_user_may_not_run(self):
        with FakeUser(self, user("reports")):
            catalogue = TK.catalogue()
        self.assertIn("sales_by_customer", catalogue)
        self.assertNotIn("list_users", catalogue)

    def test_ai_fallback_is_not_offered_to_a_restricted_user(self):
        with FakeUser(self, user("vouchers")):
            reply = CR.ask_permission("what is the meaning of life")
        self.assertEqual(reply["intent"], "permission_denied")

    def test_toolkit_run_raises(self):
        with FakeUser(self, user("reports")):
            with self.assertRaises(P.PermissionDenied):
                TK.run("list_users", {}, company_id=1)


if __name__ == "__main__":
    unittest.main()
