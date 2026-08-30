"""The skills layer has to route to the right playbook and stay wired in.

No network here: these tests are about which guidance a question attracts and
whether the two prompts still assemble around it, not about what the model
then does with it.
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from accounting_app import ai_sql
from accounting_app import chat_router as CR
from accounting_app import chat_skills as SK
from accounting_app import chat_toolkit as TK


def top(question):
    matched = SK.match(question)
    return matched[0].name if matched else None


class TestMatching(unittest.TestCase):

    def test_each_subject_reaches_its_own_skill(self):
        cases = {
            "who owes us money": "receivables_and_payables",
            "receivables ageing": "receivables_and_payables",
            "sales by customer in 2024": "sales_analysis",
            "purchases by supplier": "purchase_analysis",
            "closing stock value": "inventory_and_stock",
            "net profit last year": "profit_and_performance",
            "how much VAT do we owe this quarter": "vat_and_tax",
            "cash balance": "cash_and_bank",
            "statement of ABC Trading": "ledgers_and_statements",
            "trial balance as of today": "financial_statements",
            "what did we spend on rent": "expenses_and_cost_centers",
        }
        for question, expected in cases.items():
            self.assertEqual(top(question), expected, question)

    def test_a_core_word_outranks_several_supporting_ones(self):
        # "quarter" and "owe" both lean elsewhere; "VAT" settles it.
        self.assertEqual(top("how much VAT do we owe this quarter"), "vat_and_tax")

    def test_no_guidance_for_a_question_about_nothing(self):
        for question in ("hello", "thanks", "help", ""):
            self.assertEqual(SK.match(question), [], question)
            self.assertEqual(SK.picker_guidance(question), "", question)

    def test_at_most_two_playbooks(self):
        # A long question touching many subjects must not drown the prompt.
        question = ("sales and purchases and stock and vat and profit and cash "
                    "and receivables for this financial year")
        self.assertLessEqual(len(SK.match(question)), SK.MAX_SKILLS)

    def test_word_boundaries(self):
        # "vat" inside "private", "fy" inside "notify".
        self.assertNotIn("vat_and_tax", [s.name for s in SK.match("private ledger")])
        self.assertNotIn("periods_and_years", [s.name for s in SK.match("notify me")])


class TestGuidance(unittest.TestCase):

    def test_every_named_tool_exists(self):
        # A playbook that recommends a tool the registry lost would send the
        # picker after a name it can never return.
        for skill in SK.SKILLS:
            for name in skill.tools:
                self.assertIn(name, TK.TOOLS, f"{skill.name} -> {name}")

    def test_sql_guidance_always_carries_the_house_rules(self):
        for question in ("hello", "sales by customer", "who owes us"):
            self.assertIn("House rules", SK.sql_guidance(question), question)

    def test_picker_guidance_names_the_subject_tools(self):
        guidance = SK.picker_guidance("who owes us money")
        self.assertIn("outstanding_receivables", guidance)


class TestWiring(unittest.TestCase):
    """The prompts still format once the skills placeholder is in them."""

    def test_sql_prompt_assembles(self):
        prompt = ai_sql.SQL_SYSTEM_PROMPT.format(
            schema=ai_sql.SCHEMA_SUMMARY,
            today=datetime.date.today().isoformat(),
            company_id=1,
            max_rows=ai_sql.MAX_ROWS,
            skills=SK.sql_guidance("sales by customer in 2024"),
        )
        self.assertIn("House rules", prompt)
        self.assertIn("Per customer:", prompt)

    def test_picker_prompt_assembles(self):
        prompt = CR.PICKER_PROMPT.format(
            catalogue="list_ledgers() [Masters] - accounts",
            today="2025-01-01",
            history="(nothing yet)",
            skills=SK.picker_guidance("receivables ageing"),
        )
        self.assertIn("outstanding_receivables", prompt)
        # The JSON examples in the prompt must survive .format()
        self.assertIn('{"tool":', prompt)


if __name__ == "__main__":
    unittest.main()
