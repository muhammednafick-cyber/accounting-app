"""Routing and argument-resolution tests for the coded chat assistant.

These cover the parts that decide *what* runs - period parsing, name matching,
rule matching and follow-up continuity. They touch no database, so they run
anywhere.
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from accounting_app import chat_resolver as R
from accounting_app import chat_router as CR
from accounting_app import chat_toolkit as TK


class TestPeriodParsing(unittest.TestCase):

    def test_calendar_year(self):
        self.assertEqual(R.parse_period("2024"), ("2024-01-01", "2024-12-31", "2024"))

    def test_named_month(self):
        self.assertEqual(R.parse_period("August 2025"),
                         ("2025-08-01", "2025-08-31", "August 2025"))

    def test_quarter(self):
        self.assertEqual(R.parse_period("q1 2024"),
                         ("2024-01-01", "2024-03-31", "Q1 2024"))

    def test_explicit_range(self):
        self.assertEqual(R.parse_period("01-01-2025 to 30-06-2025"),
                         ("2025-01-01", "2025-06-30", "2025-01-01 to 2025-06-30"))

    def test_all_time_is_open_ended(self):
        self.assertEqual(R.parse_period("all time"), (None, None, "all time"))

    def test_as_of_leaves_the_start_open(self):
        start, end, _ = R.parse_period("as of 31-12-2024")
        self.assertIsNone(start)
        self.assertEqual(end, "2024-12-31")

    def test_no_period_at_all(self):
        self.assertIsNone(R.parse_period("trial balance"))

    def test_extracted_from_a_sentence(self):
        period, remainder = R.extract_period("sales by customer in 2024")
        self.assertEqual(period, ("2024-01-01", "2024-12-31", "2024"))
        self.assertEqual(remainder, "sales by customer")

    def test_relative_days(self):
        start, end, label = R.parse_period("last 7 days")
        self.assertEqual(label, "last 7 days")
        self.assertEqual(end, datetime.date.today().strftime("%Y-%m-%d"))


class TestNameMatching(unittest.TestCase):

    LEDGERS = ["ABC Trading LLC", "ABC Holdings", "XYZ Suppliers", "Cash", "Bank Account"]

    def test_exact_match(self):
        self.assertEqual(R.match_name("Cash", self.LEDGERS), "Cash")

    def test_case_insensitive(self):
        self.assertEqual(R.match_name("xyz suppliers", self.LEDGERS), "XYZ Suppliers")

    def test_unique_substring(self):
        self.assertEqual(R.match_name("xyz", self.LEDGERS), "XYZ Suppliers")

    def test_ambiguous_names_ask_rather_than_guess(self):
        with self.assertRaises(R.Ambiguous) as caught:
            R.match_name("ABC", self.LEDGERS)
        self.assertEqual(sorted(caught.exception.options),
                         ["ABC Holdings", "ABC Trading LLC"])

    def test_a_typo_still_resolves_when_only_one_name_is_close(self):
        self.assertEqual(R.match_name("XYZ Suppliars", self.LEDGERS), "XYZ Suppliers")

    def test_a_name_that_is_not_there_is_reported_as_missing(self):
        with self.assertRaises(R.NotFound) as caught:
            R.match_name("Zeta Motors", self.LEDGERS)
        self.assertEqual(caught.exception.term, "Zeta Motors")


class TestRuleMatching(unittest.TestCase):

    def route(self, question, state=None):
        matched = CR.match_rules(question, state or {})
        return matched[0] if matched else None

    def test_reports(self):
        cases = {
            "trial balance": "trial_balance",
            "balance sheet as of 31-12-2024": "balance_sheet",
            "profit and loss for 2024": "profit_and_loss",
            "cash flow for 2024": "cash_flow",
            "vat summary this quarter": "vat_summary",
        }
        for question, expected in cases.items():
            self.assertEqual(self.route(question), expected, question)

    def test_masters(self):
        cases = {
            "list all ledgers": "list_ledgers",
            "details of ABC Trading": "ledger_master_details",
            "list all items": "list_items",
            "details of item Cement": "item_master_details",
            "list locations": "list_locations",
            "fixed asset register": "fixed_asset_register",
        }
        for question, expected in cases.items():
            self.assertEqual(self.route(question), expected, question)

    def test_vouchers(self):
        cases = {
            "show voucher SAL-00001": "voucher_details",
            "sales vouchers this month": "list_vouchers",
            "purchase register 2024": "voucher_register",
            "day book for 15-08-2025": "day_book",
            "audit trail": "audit_trail",
        }
        for question, expected in cases.items():
            self.assertEqual(self.route(question), expected, question)

    def test_analysis(self):
        cases = {
            "sales by customer": "sales_by_customer",
            "monthly sales": "sales_by_month",
            "top 5 customers": "top_customers",
            "purchases by supplier": "purchases_by_supplier",
            "item profitability": "item_profitability",
            "expense breakdown": "expense_breakdown",
        }
        for question, expected in cases.items():
            self.assertEqual(self.route(question), expected, question)

    def test_general_ledger_is_a_report_not_an_account_called_general(self):
        self.assertEqual(self.route("general ledger for 2024"), "gl_dump")

    def test_voucher_number_and_limit_are_extracted(self):
        _, args = CR.match_rules("show voucher SAL-00001", {})
        self.assertEqual(args["voucher_number"], "SAL-00001")
        _, args = CR.match_rules("top 12 customers", {})
        self.assertEqual(args["limit"], 12)


class TestPluralBreakdowns(unittest.TestCase):
    """"by customers" must behave exactly like "by customer".

    The original pattern ended in \\b straight after the singular word, which
    can never match inside "customers" - so every plural phrasing fell through
    to "sales to a party called customers".
    """

    CASES = {
        "sales by customer": "sales_by_customer",
        "sales by customers": "sales_by_customer",
        "purchases by supplier": "purchases_by_supplier",
        "purchases by suppliers": "purchases_by_supplier",
        "purchase by vendors": "purchases_by_supplier",
        "sales by item": "sales_by_item",
        "sales by items": "sales_by_item",
        "sales by location": "sales_by_location",
        "sales by locations": "sales_by_location",
        "customer-wise sales": "sales_by_customer",
        "customers-wise sales": "sales_by_customer",
    }

    def test_singular_and_plural_route_the_same(self):
        for question, expected in self.CASES.items():
            matched = CR.match_rules(question, {})
            self.assertIsNotNone(matched, question)
            self.assertEqual(matched[0], expected, question)


class TestGenericTerms(unittest.TestCase):
    """A category word must never be resolved as one account or item."""

    def test_category_words_are_recognised(self):
        for word in ("vendors", "customers", "suppliers", "items", "goods",
                     "parties", "accounts", "all"):
            self.assertTrue(R.is_generic(word), word)

    def test_real_names_are_not_categories(self):
        for name in ("ABC Trading", "Cash", "7DAYS CAKE", "Rent expenses"):
            self.assertFalse(R.is_generic(name), name)

    def test_matching_a_category_raises_rather_than_guessing(self):
        with self.assertRaises(R.GenericTerm):
            R.match_name("vendors", ["Inventory", "ABC Trading"])

    def test_the_vendors_inventory_confusion_cannot_recur(self):
        """'vendors' scores 0.63 against 'Inventory' - too weak to accept."""
        with self.assertRaises((R.GenericTerm, R.NotFound)):
            R.match_name("vendors", ["Inventory"])


class TestFuzzyThreshold(unittest.TestCase):

    LEDGERS = ["Inventory", "XYZ Suppliers", "Cash", "Rent expenses"]

    def test_a_close_typo_is_accepted(self):
        self.assertEqual(R.match_name("XYZ Suppliars", self.LEDGERS),
                         "XYZ Suppliers")

    def test_a_weak_single_match_is_offered_not_applied(self):
        with self.assertRaises(R.NotFound) as caught:
            R.match_name("Inventry Holdings Ltd", self.LEDGERS)
        self.assertTrue(caught.exception.suggestions)


class TestFollowUps(unittest.TestCase):

    STATE = {
        "last_tool": "sales_total",
        "last_period": ("2024-01-01", "2024-12-31", "2024"),
        "last_ledger": "ABC Trading",
        "last_item": None,
        "last_token": "token",
    }

    def follow(self, question):
        return CR.match_followup(CR._norm(question), question, self.STATE)

    def test_breakdown_inherits_the_period(self):
        tool, args = self.follow("break it by customer")
        self.assertEqual(tool, "sales_by_customer")
        self.assertTrue(args["inherit_period"])

    def test_new_period_reuses_the_previous_tool(self):
        tool, args = self.follow("what about 2023?")
        self.assertEqual(tool, "sales_total")
        self.assertEqual(args["_period"][0], "2023-01-01")

    def test_limit_only(self):
        tool, args = self.follow("only the top 5")
        self.assertEqual(tool, "sales_total")
        self.assertEqual(args["limit"], 5)

    def test_pronoun_carries_the_party_to_a_new_tool(self):
        tool, args = self.follow("his outstanding")
        self.assertEqual(tool, "ledger_balance")
        self.assertEqual(args["ledger"], "ABC Trading")

    def test_export_of_the_previous_answer(self):
        tool, _ = CR.match_rules("give me that in excel", self.STATE)
        self.assertEqual(tool, "__export_last")

    def test_export_needs_something_to_export(self):
        self.assertIsNone(CR.match_rules("give me that in excel", {}))


class TestPendingAnswers(unittest.TestCase):
    """A question the assistant asked must not swallow the next message."""

    def test_a_real_period_answers_a_period_question(self):
        self.assertTrue(CR._answers_the_question("period", "last month", {}))
        self.assertTrue(CR._answers_the_question("period", "August 2025", {}))

    def test_a_new_request_does_not_answer_a_period_question(self):
        self.assertFalse(
            CR._answers_the_question("period", "which buyer is flakiest", {}))
        self.assertFalse(CR._answers_the_question("period", "cash balance", {}))

    def test_a_recognised_report_does_not_answer_a_name_question(self):
        self.assertFalse(CR._answers_the_question("ledger", "trial balance", {}))

    def test_a_short_name_does_answer_a_name_question(self):
        self.assertTrue(CR._answers_the_question("ledger", "ABC Trading", {}))

    def test_a_voucher_number_question_needs_a_voucher_number(self):
        self.assertTrue(CR._answers_the_question("voucher_number", "SAL-00001", {}))
        self.assertFalse(CR._answers_the_question("voucher_number", "no idea", {}))


class TestAiOnlyMode(unittest.TestCase):
    """The "AI only" checkbox: every question goes to the model instead."""

    def setUp(self):
        self.calls = []
        self._real_fallback = CR.run_ai_fallback
        self._real_key = None
        CR.run_ai_fallback = lambda q, c: (self.calls.append(q),
                                           CR.plain("ai answer", "ai"))[1]
        import accounting_app.chatbot_service as CS
        self._real_key = CS.get_openrouter_api_key
        CS.get_openrouter_api_key = lambda: "test-key"

    def tearDown(self):
        CR.run_ai_fallback = self._real_fallback
        import accounting_app.chatbot_service as CS
        CS.get_openrouter_api_key = self._real_key

    def test_a_question_the_rules_know_still_goes_to_ai(self):
        answer = CR.route_ai_only("trial balance", 1)
        self.assertEqual(answer["intent"], "ai")
        self.assertEqual(self.calls, ["trial balance"])

    def test_greetings_and_help_go_to_ai_too(self):
        for question in ("hello", "help", "total sales"):
            CR.route_ai_only(question, 1)
        self.assertEqual(self.calls, ["hello", "help", "total sales"])

    def test_reset_stays_local(self):
        # The model cannot clear the conversation, so this is not handed over.
        self.assertIsNone(CR.route_ai_only("reset", 1))
        self.assertEqual(self.calls, [])

    def test_a_missing_api_key_is_explained_not_silently_ignored(self):
        import accounting_app.chatbot_service as CS
        CS.get_openrouter_api_key = lambda: None
        answer = CR.route_ai_only("total sales", 1)
        self.assertEqual(answer["intent"], "error")
        self.assertIn("AI Settings", answer["response"])
        self.assertEqual(self.calls, [])


class TestRegistry(unittest.TestCase):

    def test_every_rule_points_at_a_real_tool(self):
        questions = [q for tool in TK.TOOLS.values() for q in tool.examples]
        for question in questions:
            matched = CR.match_rules(question, {})
            if matched and matched[0] != "__export_last":
                self.assertIn(matched[0], TK.TOOLS, question)

    def test_catalogue_lists_every_tool(self):
        catalogue = TK.catalogue()
        for name in TK.TOOLS:
            self.assertIn(name + "(", catalogue)

    def test_declared_parameters_are_all_understood(self):
        known = {"period", "date", "ledger", "party", "customer", "supplier",
                 "account", "item", "product", "location", "cost_center", "group",
                 "voucher_type", "voucher_number", "limit", "text", "nature",
                 "days", "min_amount"}
        for tool in TK.TOOLS.values():
            for param in tool.param_names:
                self.assertIn(param, known, f"{tool.name} declares '{param}'")


if __name__ == "__main__":
    unittest.main()
