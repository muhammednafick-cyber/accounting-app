"""The second assistant: the loop, and the guarantees it inherits.

The model is replaced throughout - these test the driving, not OpenRouter.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from accounting_app import chat_agent as A
from accounting_app import chat_permissions as P
from accounting_app import chat_toolkit as TK


def call(name, arguments="{}", call_id="c1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": arguments}}


def table(title, rows):
    return {"title": title, "columns": ["Ledger", "Amount"], "rows": rows,
            "summary": title + " summary"}


class FakeModel(object):
    """Returns the scripted replies in order, and records what it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []

    def __call__(self, messages, tools):
        self.seen.append((list(messages), tools))
        if not self.replies:
            return {"content": "done", "tool_calls": []}
        return self.replies.pop(0)


class LoopTests(unittest.TestCase):

    def setUp(self):
        self._run = TK.run
        self._ask = A._ask_model
        self._allowed = P.allowed_tool_names
        self._sql = P.can_use_ai_sql
        self.ran = []

        def fake_run(name, args, company_id=None, state=None):
            self.ran.append((name, args))
            return table(name, [["Cash", "100.00"]]), args

        TK.run = fake_run
        P.allowed_tool_names = lambda: ["cash_balance", "bank_balance"]
        P.can_use_ai_sql = lambda: False

    def tearDown(self):
        TK.run = self._run
        A._ask_model = self._ask
        P.allowed_tool_names = self._allowed
        P.can_use_ai_sql = self._sql

    def test_it_keeps_going_across_several_tools(self):
        # The whole point of the new assistant: one question, more than one
        # report, an answer that arrives only when the model stops asking.
        A._ask_model = FakeModel(
            {"content": "", "tool_calls": [call("cash_balance")]},
            {"content": "", "tool_calls": [call("bank_balance", call_id="c2")]},
            {"content": "Cash and bank together are 200.", "tool_calls": []},
        )
        reply = A.run("how much money do we have", company_id=1)

        self.assertEqual([n for n, _ in self.ran], ["cash_balance", "bank_balance"])
        self.assertEqual(reply["data"]["tools_used"], ["cash_balance", "bank_balance"])
        self.assertEqual(reply["data"]["steps"], 3)
        # Both tables are shown, not just the last one.
        self.assertIn("cash_balance", reply["response"])
        self.assertIn("bank_balance", reply["response"])
        self.assertIn("Cash and bank together are 200.", reply["response"])

    def test_the_model_sees_what_the_tool_returned(self):
        model = FakeModel(
            {"content": "", "tool_calls": [call("cash_balance")]},
            {"content": "There is 100 in cash.", "tool_calls": []},
        )
        A._ask_model = model
        A.run("cash balance", company_id=1)

        second_call_messages = model.seen[1][0]
        tool_message = [m for m in second_call_messages if m["role"] == "tool"][0]
        self.assertIn("Cash", tool_message["content"])
        self.assertIn("100.00", tool_message["content"])
        self.assertEqual(tool_message["tool_call_id"], "c1")

    def test_a_failing_tool_does_not_end_the_conversation(self):
        # One bad ledger name should cost a step, not the answer.
        def exploding_run(name, args, company_id=None, state=None):
            self.ran.append((name, args))
            if len(self.ran) == 1:
                raise ValueError("no such ledger 'Csah'")
            return table(name, [["Cash", "100.00"]]), args

        TK.run = exploding_run
        model = FakeModel(
            {"content": "", "tool_calls": [call("cash_balance", '{"ledger": "Csah"}')]},
            {"content": "", "tool_calls": [call("cash_balance", '{"ledger": "Cash"}',
                                                call_id="c2")]},
            {"content": "It is 100.", "tool_calls": []},
        )
        A._ask_model = model
        reply = A.run("balance of Csah", company_id=1)

        failed = [m for m in model.seen[1][0] if m["role"] == "tool"][0]
        self.assertIn("no such ledger", failed["content"])
        self.assertEqual(reply["data"]["tools_used"], ["cash_balance"])
        self.assertIn("It is 100.", reply["response"])

    def test_a_tool_the_user_may_not_run_is_refused_not_raised(self):
        def denying_run(name, args, company_id=None, state=None):
            raise P.PermissionDenied(name, "reports", "Reports")

        TK.run = denying_run
        model = FakeModel(
            {"content": "", "tool_calls": [call("cash_balance")]},
            {"content": "You don't have access to that.", "tool_calls": []},
        )
        A._ask_model = model
        reply = A.run("cash balance", company_id=1)

        denied = [m for m in model.seen[1][0] if m["role"] == "tool"][0]
        self.assertIn("permission", denied["content"].lower())
        self.assertEqual(reply["data"]["tools_used"], [])

    def test_it_stops_after_the_step_cap(self):
        # A model that never stops asking must not run up an unbounded bill.
        class Forever(object):
            def __call__(self, messages, tools):
                return {"content": "", "tool_calls": [call("cash_balance")]}

        A._ask_model = Forever()
        reply = A.run("go forever", company_id=1)

        self.assertEqual(len(self.ran), A.MAX_STEPS)
        self.assertIn("stopped after", reply["response"])

    def test_the_catalogue_is_only_what_this_user_may_run(self):
        names = [t["function"]["name"] for t in A._catalogue()]
        self.assertEqual(names, ["cash_balance", "bank_balance"])

        P.can_use_ai_sql = lambda: True
        self.assertIn("query_database",
                      [t["function"]["name"] for t in A._catalogue()])

    def test_it_says_so_rather_than_answering_with_no_tools(self):
        P.allowed_tool_names = lambda: []
        reply = A.run("anything", company_id=1)
        self.assertEqual(reply["intent"], "permission_denied")


class SafetyTests(unittest.TestCase):
    """The two properties the old assistant is trusted for."""

    def setUp(self):
        self._run, self._ask = TK.run, A._ask_model
        self._allowed, self._sql = P.allowed_tool_names, P.can_use_ai_sql
        TK.run = lambda name, args, company_id=None, state=None: (
            table(name, [["Cash", "1,234.56"]]), args)
        P.allowed_tool_names = lambda: ["cash_balance"]
        P.can_use_ai_sql = lambda: False

    def tearDown(self):
        TK.run, A._ask_model = self._run, self._ask
        P.allowed_tool_names, P.can_use_ai_sql = self._allowed, self._sql

    def test_figures_shown_come_from_the_tool_not_the_model(self):
        A._ask_model = FakeModel(
            {"content": "", "tool_calls": [call("cash_balance")]},
            {"content": "Cash is 9,999.00.", "tool_calls": []},
        )
        reply = A.run("cash balance", company_id=1)
        # The real figure is in the rendered table regardless of what the model
        # wrote, and the model's sentence is marked as its own.
        self.assertIn("1,234.56", reply["response"])
        self.assertIn("rv-src-ai", reply["response"])

    def test_the_models_prose_cannot_inject_markup(self):
        A._ask_model = FakeModel(
            {"content": "", "tool_calls": [call("cash_balance")]},
            {"content": "<script>alert(1)</script> and <b>bold</b>",
             "tool_calls": []},
        )
        reply = A.run("cash balance", company_id=1)
        self.assertNotIn("<script>", reply["response"])
        self.assertIn("&lt;script&gt;", reply["response"])


class SeparationTests(unittest.TestCase):
    """The old assistant has to be unaffected and remain the default."""

    def test_the_old_endpoint_is_still_there_and_separate(self):
        import app as appmod

        rules = {r.rule for r in appmod.app.url_map.iter_rules()}
        self.assertIn("/api/chat_query", rules)
        self.assertIn("/api/chat_agent", rules)

    def test_the_old_router_does_not_reach_the_agent(self):
        # If the old path ever imported this module, "revert by deleting it"
        # would stop being true.
        import io
        for name in ("chat_router.py", "chat_routes.py", "chatbot_service.py"):
            with io.open(os.path.join(os.path.dirname(__file__), '..',
                                      'accounting_app', name),
                         encoding='utf-8') as f:
                self.assertNotIn("chat_agent", f.read(), name)

    def test_the_agent_offers_no_way_to_write(self):
        import io
        with io.open(os.path.join(os.path.dirname(__file__), '..',
                                  'accounting_app', 'chat_agent.py'),
                     encoding='utf-8') as f:
            source = f.read()
        for writer in ("propose_voucher", "propose_purchase", "INSERT", "UPDATE "):
            self.assertNotIn(writer, source, writer)


if __name__ == "__main__":
    unittest.main()
