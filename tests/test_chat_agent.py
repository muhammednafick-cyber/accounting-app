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


class DownloadTests(unittest.TestCase):
    """Every table the agent shows has to be downloadable.

    The first version parked all the tables but printed one button at the
    bottom, so clicking it handed over whichever report happened to run last
    rather than the one being pointed at.
    """

    def setUp(self):
        import app as appmod

        self._run, self._ask = TK.run, A._ask_model
        self._allowed, self._sql = P.allowed_tool_names, P.can_use_ai_sql
        TK.run = lambda name, args, company_id=None, state=None: (
            table(name.replace("_", " ").title(), [["Cash", "100.00"]]), args)
        P.allowed_tool_names = lambda: ["cash_balance", "bank_balance"]
        P.can_use_ai_sql = lambda: False
        self.ctx = appmod.app.test_request_context('/api/chat_agent', method='POST')
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()
        TK.run, A._ask_model = self._run, self._ask
        P.allowed_tool_names, P.can_use_ai_sql = self._allowed, self._sql

    def _tokens(self, html):
        import re
        return re.findall(r"token=([0-9a-f]+)", html)

    def test_each_table_gets_its_own_download(self):
        A._ask_model = FakeModel(
            {"content": "", "tool_calls": [call("cash_balance")]},
            {"content": "", "tool_calls": [call("bank_balance", call_id="c2")]},
            {"content": "Together that is 300.", "tool_calls": []},
        )
        reply = A.run("cash and bank", company_id=1)
        self.assertEqual(len(set(self._tokens(reply["response"]))), 2)

    def test_a_parked_result_can_be_fetched_back(self):
        from accounting_app.chat_export_store import load

        A._ask_model = FakeModel(
            {"content": "", "tool_calls": [call("cash_balance")]},
            {"content": "There it is.", "tool_calls": []},
        )
        reply = A.run("cash balance", company_id=1)
        stored = load(reply["data"]["export_token"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored["rows"], [["Cash", "100.00"]])

    def test_asking_for_excel_afterwards_costs_no_model_call(self):
        # "give it in excel" is not a question, and the old assistant answers
        # it locally. This one must too, or the model has to think to offer a
        # download and usually does not.
        A._ask_model = FakeModel(
            {"content": "", "tool_calls": [call("cash_balance")]},
            {"content": "There it is.", "tool_calls": []},
        )
        A.run("cash balance", company_id=1)

        def explode(messages, tools):
            raise AssertionError("the model was called for a download")

        A._ask_model = explode
        reply = A.run("give it in excel", company_id=1)
        self.assertEqual(reply["intent"], "export_chat_result")
        self.assertIn("token=", reply["response"])

    def test_the_named_format_is_the_one_offered_first(self):
        A._ask_model = FakeModel(
            {"content": "", "tool_calls": [call("cash_balance")]},
            {"content": "There it is.", "tool_calls": []},
        )
        A.run("cash balance", company_id=1)
        A._ask_model = FakeModel()
        for phrase, fmt in (("csv please", "csv"), ("download pdf", "pdf")):
            reply = A.run(phrase, company_id=1)
            self.assertIn("format=%s" % fmt, reply["response"], phrase)

    def test_a_real_question_is_not_mistaken_for_a_download(self):
        for phrase in ("show me the excel import log", "top 5 customers",
                       "what about last year"):
            self.assertFalse(A._is_export_request(phrase), phrase)

    def test_export_phrases_are_recognised(self):
        for phrase in ("give it in excel", "in excel", "csv", "download pdf",
                       "excel", "give me that in excel"):
            self.assertTrue(A._is_export_request(phrase), phrase)

    def test_nothing_to_export_falls_through_to_a_normal_answer(self):
        # Asking for a download before anything has been answered must reach
        # the model, not produce a dead link.
        A._ask_model = FakeModel({"content": "Ask me something first.",
                                  "tool_calls": []})
        reply = A.run("give it in excel", company_id=1)
        self.assertNotEqual(reply["intent"], "export_chat_result")


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
