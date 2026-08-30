"""The rule-based General Chat: what it recognises, and what it offers.

The corpus below is the point of this file. It is the questions a real user
types, in their words rather than ours, and it is what stops the phrasebook
quietly rotting as tools are renamed or patterns edited. Before the phrasebook
existed the rules answered 56% of it; anything under 90% here is a regression.

No database and no network: every assertion is about routing.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from accounting_app import chat_phrasebook as PB
from accounting_app import chat_router as CR
from accounting_app import chat_toolkit as TK


# The question, and the report it ought to reach.
CORPUS = {
    "outstanding_receivables": [
        "how much money do customers owe us", "who owes us money", "receivables",
        "outstanding from customers", "customer outstanding", "total receivables",
        "how much is due from customers", "which customers have not paid",
        "debtors list", "accounts receivable", "customer wise outstanding",
        "overdue customers", "aged debtors", "how much do we have to collect",
        "collection pending", "unpaid invoices",
    ],
    "outstanding_payables": [
        "how much do we owe suppliers", "payables", "outstanding to suppliers",
        "supplier outstanding", "creditors", "accounts payable", "who do we owe",
        "pending payments to suppliers", "how much to pay vendors", "aged creditors",
    ],
    "sales_total": [
        "total sales", "what is my sales", "how much did we sell", "sales figure",
        "revenue", "turnover", "how much business did we do", "gross sales",
    ],
    "sales_by_customer": [
        "sales by customer", "customer wise sales", "sales per customer",
        "which customer bought the most", "party wise sales",
    ],
    "top_customers": [
        "top customers", "best customers", "biggest customers", "top 10 customers",
        "who are my main customers", "largest buyers",
    ],
    "profit_and_loss": [
        "profit and loss", "p&l", "income statement", "are we profitable",
        "show me the p and l", "profit loss statement",
    ],
    "net_profit": [
        "net profit", "what is my profit", "how much profit did we make",
        "bottom line", "did we make money",
    ],
    "trial_balance": ["trial balance", "tb", "show trial balance"],
    "balance_sheet": ["balance sheet", "bs", "financial position"],
    "cash_balance": [
        "cash balance", "how much cash do we have", "cash in hand",
        "available cash", "cash position", "money in hand",
    ],
    "bank_balance": ["bank balance", "how much in the bank", "bank position"],
    "closing_stock_value": [
        "closing stock", "stock value", "inventory value",
        "how much stock do we have", "value of inventory", "stock in hand",
        "closing inventory",
    ],
    "vat_summary": [
        "vat summary", "how much vat do we owe", "vat payable", "net vat",
        "tax payable", "vat position",
    ],
    "ledger_statement": [
        "statement of ABC Trading", "ledger of ABC Trading",
        "transactions of ABC Trading",
    ],
    "ledger_balance": [
        "balance of ABC Trading", "what is ABC Trading balance",
        "how much does ABC Trading owe",
    ],
    "expense_total": [
        "total expenses", "how much did we spend", "spending",
    ],
    "expense_breakdown": [
        "expense breakdown", "what did we spend on", "expenses by category",
        "where is money going",
    ],
    "list_items": [
        "list items", "show all items", "what products do we have", "item list",
    ],
    "list_ledgers": ["list ledgers", "all accounts", "ledger list"],
    "day_book": ["day book", "todays entries", "what was posted today"],
    "last_vouchers": ["last vouchers", "recent entries", "latest transactions"],
    "item_stock": ["stock of Cement", "how much Cement do we have"],
    "kpi_summary": ["kpi", "business summary", "overview", "how are we doing"],
    "cash_flow": ["cash flow", "cash flow statement", "where did the cash go"],
    "slow_moving_items": [
        "slow moving items", "dead stock", "items not selling", "non moving stock",
    ],
    "purchases_by_supplier": ["purchases by supplier", "vendor wise purchases"],
    "purchase_total": ["total purchases", "how much did we buy"],
    "sales_by_month": ["monthly sales", "sales by month", "sales trend"],
    "vat_detailed": ["detailed vat", "vat details", "vat line by line"],
    "inventory_ageing": ["inventory ageing", "how old is my stock"],
    "negative_stock": ["negative stock", "items with negative stock", "minus stock"],
    "fixed_asset_register": ["fixed assets", "asset register", "list of assets"],
    "list_users": ["list users", "who are the users", "user list"],
    "company_settings": ["company settings", "company details"],
}


def route_of(question):
    """The tool a question reaches without any AI, or None."""
    matched = PB.match(question) or CR.match_rules(question, {})
    return matched[0] if matched else None


class TestCoverage(unittest.TestCase):

    def test_the_corpus_is_answered_without_ai(self):
        misses = []
        total = 0
        for expected, questions in CORPUS.items():
            for question in questions:
                total += 1
                got = route_of(question)
                if got != expected:
                    misses.append((question, expected, got))

        rate = (total - len(misses)) * 100 // total
        self.assertGreaterEqual(
            rate, 90,
            "coverage fell to %d%%; misses:\n%s"
            % (rate, "\n".join("  %r wanted %s got %s" % m for m in misses)))

    def test_no_question_is_answered_with_the_wrong_report(self):
        # A wrong report is worse than no answer: it looks authoritative.
        wrong = [(q, e, route_of(q)) for e, qs in CORPUS.items() for q in qs
                 if route_of(q) is not None and route_of(q) != e]
        self.assertLessEqual(
            len(wrong), 2,
            "questions answered with the wrong report:\n%s"
            % "\n".join("  %r wanted %s got %s" % w for w in wrong))


class TestPrecedence(unittest.TestCase):
    """A whole phrase beats a single word the old rules keyed on."""

    def test_specific_phrases_win(self):
        cases = {
            "income statement": "profit_and_loss",       # not ledger_statement
            "profit loss statement": "profit_and_loss",
            "cash flow statement": "cash_flow",
            "financial position": "balance_sheet",
            "vat payable": "vat_summary",                # not outstanding_payables
            "tax payable": "vat_summary",
            "how much vat do we owe": "vat_summary",
        }
        for question, expected in cases.items():
            self.assertEqual(route_of(question), expected, question)

    def test_a_reserved_word_is_not_a_party_name(self):
        # These used to resolve to an account or item of that name.
        cases = {
            "todays entries": "day_book",
            "recent entries": "last_vouchers",
            "dead stock": "slow_moving_items",
            "negative stock": "negative_stock",
            "company details": "company_settings",
            "how old is my stock": "inventory_ageing",
        }
        for question, expected in cases.items():
            self.assertEqual(route_of(question), expected, question)

    def test_a_real_name_still_captures(self):
        self.assertEqual(PB.match("statement of ABC Trading"),
                         ("ledger_statement", {"ledger": "ABC Trading"}))
        self.assertEqual(PB.match("stock of Cement"),
                         ("item_stock", {"item": "Cement"}))

    def test_politeness_is_ignored(self):
        for question in ("can you please show me the total sales",
                         "i want total sales", "please give me total sales",
                         "total sales please"):
            self.assertEqual(route_of(question), "sales_total", question)

    def test_the_period_survives(self):
        tool, args = PB.match("total sales in 2024")
        self.assertEqual(tool, "sales_total")
        self.assertEqual(args["_period"][:2], ("2024-01-01", "2024-12-31"))


class TestItemVersusCompanyStock(unittest.TestCase):
    """Naming an item must narrow the answer to that item.

    "closing inventory value of item swimming glass on 31.12.2023" used to
    match on the words "inventory value" alone: the item name was dropped and
    the whole company's closing stock came back under the heading the user had
    asked about, which reads as an answer rather than a mistake.
    """

    def route(self, question):
        matched = PB.match(question) or CR.match_rules(question, {})
        return matched if matched else (None, {})

    def test_a_named_item_reaches_the_item_report(self):
        for question in (
                "closing inventory value of item swimming glass on 31.12.2023",
                "closing stock of swimming glass on 31.12.2023",
                "closing stock value of swimming glass as of 31-12-2023",
                "inventory value of item Cement on 30-06-2025",
                "stock value of swimming glass",
                "closing inventory for swimming glass"):
            tool, args = self.route(question)
            self.assertEqual(tool, "item_stock", question)
            self.assertTrue(args.get("item"), "no item captured from %r" % question)

    def test_the_item_and_the_date_both_survive(self):
        tool, args = self.route(
            "closing inventory value of item swimming glass on 31.12.2023")
        self.assertEqual(tool, "item_stock")
        self.assertEqual(args["item"], "swimming glass")
        self.assertEqual(args["_period"][1], "2023-12-31")

    def test_without_an_item_it_stays_company_wide(self):
        for question in ("closing inventory value on 31.12.2023", "closing stock",
                         "stock value", "inventory value as of 31-12-2024",
                         "value of inventory", "stock on hand", "total stock"):
            tool, _ = self.route(question)
            self.assertEqual(tool, "closing_stock_value", question)


class TestStockAtALocation(unittest.TestCase):
    """"stock in Abu Dhabi" reached nothing, and naming both an item and a
    location read the whole phrase as one item name."""

    def route(self, question):
        matched = PB.match(question) or CR.match_rules(question, {})
        return matched if matched else (None, {})

    def test_a_location_on_its_own(self):
        for question in ("stock in Abu Dhabi", "stock at Abu Dhabi",
                         "stock available in Abu Dhabi"):
            tool, args = self.route(question)
            self.assertEqual(tool, "stock_by_location", question)
            self.assertEqual(args.get("location"), "Abu Dhabi", question)
            self.assertIsNone(args.get("item"), question)

    def test_an_item_at_a_location(self):
        for question in ("stock of Cement in Abu Dhabi",
                         "closing stock of Cement at Abu Dhabi",
                         "Cement stock in Abu Dhabi",
                         "how much Cement do we have in Abu Dhabi"):
            tool, args = self.route(question)
            self.assertEqual(tool, "stock_by_location", question)
            self.assertEqual(args.get("item"), "Cement", question)
            self.assertEqual(args.get("location"), "Abu Dhabi", question)

    def test_a_two_word_item_at_a_location(self):
        _, args = self.route("stock of Swimming Glass in Abu Dhabi")
        self.assertEqual(args.get("item"), "Swimming Glass")
        self.assertEqual(args.get("location"), "Abu Dhabi")

    def test_the_date_survives(self):
        tool, args = self.route("stock in Abu Dhabi as of 31-12-2023")
        self.assertEqual(tool, "stock_by_location")
        self.assertEqual(args.get("location"), "Abu Dhabi")
        self.assertEqual(args["_period"][1], "2023-12-31")

    def test_the_split_by_every_location(self):
        for question in ("stock by location", "location wise stock"):
            self.assertEqual(self.route(question)[0], "stock_by_location", question)

    def test_stock_questions_without_a_location_are_unchanged(self):
        cases = {
            "closing stock": "closing_stock_value",
            "stock on hand": "closing_stock_value",   # not a location called "hand"
            "stock in hand": "closing_stock_value",
            "stock value": "closing_stock_value",
            "stock of Cement": "item_stock",
        }
        for question, expected in cases.items():
            self.assertEqual(self.route(question)[0], expected, question)


class TestSuggestions(unittest.TestCase):

    def test_an_ambiguous_question_offers_options(self):
        labels = [label for _, label in PB.suggest("stock information")]
        self.assertGreaterEqual(len(labels), 2)
        self.assertIn("Closing stock value", labels)

    def test_options_are_capped(self):
        self.assertLessEqual(len(PB.suggest("money")), PB.MAX_OPTIONS)

    def test_nonsense_offers_nothing(self):
        # Better to fall through than to offer a report at random.
        for question in ("what is the weather", "tell me a joke", "asdfghjk",
                         "who won the world cup"):
            self.assertEqual(PB.suggest(question), [], question)

    def test_every_offered_option_is_a_question_we_answer(self):
        # A chip that does not route back is a dead button.
        for tool, label in PB.labels():
            matched = PB.match(label)
            self.assertIsNotNone(matched, "%r offers nothing" % label)
            self.assertEqual(matched[0], tool,
                             "%r is labelled %s but routes to %s"
                             % (label, tool, matched[0]))


class TestRegistry(unittest.TestCase):

    def test_every_intent_names_a_real_tool(self):
        for tool, label in PB.labels():
            self.assertIn(tool, TK.TOOLS, "%r -> %s" % (label, tool))

    def test_labels_are_unique(self):
        labels = [label for _, label in PB.labels()]
        self.assertEqual(len(labels), len(set(labels)))


if __name__ == "__main__":
    unittest.main()
