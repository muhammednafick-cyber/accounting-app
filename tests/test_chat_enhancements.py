"""The seven chat improvements: clear mode, memory, progress, next questions,
clickable cells, charts, and learning from what went unanswered.

Plus the escaping that came with them: cells, headings and summaries carry the
company's own data - some of it from supplier files - into a window that
inserts answers as HTML.
"""
import io
import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from accounting_app import chat_agent as A
from accounting_app import chat_extras as X
from accounting_app import chat_permissions as P
from accounting_app import chat_router as CR
from accounting_app import chat_toolkit as TK

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def source(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def call(name, arguments="{}", call_id="c1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": arguments}}


# ------------------------------------------------------------ 1. clear mode

class ClearModeTests(unittest.TestCase):

    def test_the_agent_takes_the_message_before_the_voucher_type_is_read(self):
        script = source("static", "script.js")
        body = script[script.index("async function sendFromInput"):]
        agent_first = body.index("if (isAgentMode())")
        voucher_type = body.index("const vt = assistantState.voucherType;")
        self.assertLess(agent_first, voucher_type)

    def test_the_voucher_selector_is_locked_while_the_agent_is_on(self):
        script = source("static", "script.js")
        self.assertIn("function syncVoucherSelectWithAgent", script)
        self.assertIn("select.disabled = agentOn;", script)


# --------------------------------------------------------------- 2. memory

class MemoryTests(unittest.TestCase):

    def test_memory_keeps_heading_summary_and_totals_not_markup(self):
        used = [("ledger_statement", {
            "title": "Statement - Nafi-ALMARAI EMIRATES COMPANY L.L.C",
            "summary": "9 transactions, closing balance <b>255,022.19 Cr</b>",
            "totals": {"Closing Balance": "255,022.19 Cr"},
        })]
        memory = A._memory(used, "They are owed 255,022.19.")
        self.assertIn("ALMARAI", memory)
        self.assertIn("255,022.19 Cr", memory)
        self.assertIn("Concluded:", memory)
        self.assertNotIn("<b>", memory)

    def test_memory_is_bounded(self):
        used = [("x", {"title": "T" * 5000})]
        self.assertLessEqual(len(A._memory(used, "")), A.MEMORY_LIMIT)


# ------------------------------------------------------------- 3. progress

class ProgressTests(unittest.TestCase):

    def setUp(self):
        self._run, self._ask = TK.run, A._ask_model
        self._allowed, self._sql = P.allowed_tool_names, P.can_use_ai_sql
        TK.run = lambda name, args, company_id=None, state=None: (
            {"title": name, "columns": ["Ledger", "Amount"],
             "rows": [["Cash", "1.00"]], "summary": "s"}, args)
        P.allowed_tool_names = lambda: ["cash_balance"]
        P.can_use_ai_sql = lambda: False

    def tearDown(self):
        TK.run, A._ask_model = self._run, self._ask
        P.allowed_tool_names, P.can_use_ai_sql = self._allowed, self._sql

    def test_each_step_is_reported_as_it_happens(self):
        replies = iter([
            {"content": "", "tool_calls": [call("cash_balance")]},
            {"content": "Done.", "tool_calls": []},
        ])
        A._ask_model = lambda messages, tools: next(replies)
        steps = []
        A.run("cash", company_id=1, progress=steps.append)
        self.assertEqual(steps[0], "Reading your question")
        self.assertTrue(any("cash balance" in s for s in steps))
        self.assertEqual(steps[-1], "Deciding what to look at next")

    def test_a_broken_progress_reporter_does_not_break_the_answer(self):
        replies = iter([{"content": "Fine.", "tool_calls": []}])
        A._ask_model = lambda messages, tools: next(replies)

        def explode(step):
            raise RuntimeError("database away")

        reply = A.run("cash", company_id=1, progress=explode)
        self.assertEqual(reply["intent"], "agent")

    def test_progress_is_readable_from_any_worker_and_only_by_its_owner(self):
        from accounting_app import chat_agent_routes as R

        run_id = uuid.uuid4().hex
        write = R._progress_writer(1, run_id)
        write("Running cash balance")
        write(None)
        from database.app_state_db import chat_export_load

        mine = chat_export_load(R._progress_key(1, run_id))
        self.assertEqual(mine["steps"], ["Running cash balance"])
        self.assertTrue(mine["done"])
        self.assertIsNone(chat_export_load(R._progress_key(2, run_id)))

    def test_a_run_id_must_look_like_one(self):
        from accounting_app import chat_agent_routes as R

        self.assertIsNone(R._run_id("../../etc"))
        self.assertIsNone(R._run_id("abc"))
        self.assertEqual(R._run_id("A" * 24), "a" * 24)


# -------------------------------------------------------- 4. next questions

class FollowUpTests(unittest.TestCase):

    def test_a_period_report_offers_other_periods(self):
        chips = X.follow_ups("sales_total", {"rows": [[1]]}, "sales total this year")
        self.assertIn("what about last year", chips)

    def test_a_total_offers_its_months(self):
        chips = X.follow_ups("sales_total", {"rows": [[1]]}, "sales total")
        self.assertIn("sales by month", chips)

    def test_never_more_than_three(self):
        rows = {"rows": [[i] for i in range(20)]}
        for name in TK.TOOLS:
            self.assertLessEqual(len(X.follow_ups(name, rows, "")), 3, name)

    def test_every_by_month_chip_is_a_phrase_the_rules_answer(self):
        from accounting_app import chat_phrasebook as PB

        for phrase in set(X.BY_MONTH.values()):
            hit = PB.match(phrase) or CR.match_rules(phrase, {})
            self.assertIsNotNone(hit, phrase)
            self.assertIn("month", hit[0], phrase)

    def test_an_unknown_tool_offers_nothing(self):
        self.assertEqual(X.follow_ups("no_such_tool", {}, ""), [])


# ---------------------------------------------------- 5. clickable cells

class ClickableCellTests(unittest.TestCase):

    def table(self, columns, rows):
        return CR.render_table({"columns": columns, "rows": rows})

    def test_a_ledger_cell_asks_for_its_statement(self):
        html = self.table(["Ledger", "Amount"], [["Nafi-Cash", 10.0]])
        self.assertIn("class='rv-ask rv-cell-link'", html)
        self.assertIn("data-value='statement of Nafi-Cash'", html)

    def test_a_voucher_cell_opens_the_voucher_only_when_it_is_one(self):
        html = self.table(["Voucher", "Amount"],
                          [["FY26-REC-000009", 5000.0], ["opening", 1.0]])
        self.assertIn("data-value='show voucher FY26-REC-000009'", html)
        self.assertEqual(html.count("rv-cell-link"), 1)

    def test_both_link_phrasings_reach_the_right_report(self):
        self.assertEqual(CR.match_rules("statement of Nafi-Cash", {})[0],
                         "ledger_statement")
        self.assertEqual(CR.match_rules("show voucher FY26-REC-000009", {})[0],
                         "voucher_details")

    def test_figures_are_never_links(self):
        html = self.table(["Ledger", "Amount"], [["Nafi-Cash", 10.0]])
        self.assertNotIn("statement of 10", html)


class EscapingTests(unittest.TestCase):

    def test_a_cell_cannot_inject_markup(self):
        html = CR.render_table({"columns": ["Narration"],
                                "rows": [["<script>alert(1)</script>"]]})
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_a_linked_cell_cannot_break_out_of_its_attribute(self):
        html = CR.render_table({"columns": ["Ledger"],
                                "rows": [["x' onmouseover='alert(1)"]]})
        self.assertNotIn("onmouseover='alert", html)

    def test_a_heading_cannot_inject_markup(self):
        html = CR.render_table({"columns": ["<img src=x onerror=alert(1)>"],
                                "rows": [["a"]]})
        self.assertNotIn("<img", html)

    def test_a_summary_keeps_its_bold_and_loses_everything_else(self):
        out = X.safe("Owed <b>1,000.00</b> by <img src=x onerror=alert(1)><br>")
        self.assertIn("<b>1,000.00</b>", out)
        self.assertIn("<br>", out)
        self.assertNotIn("<img", out)


# ---------------------------------------------------------------- 6. charts

class ChartTests(unittest.TestCase):

    def months(self, n):
        return {"columns": ["Month", "Sales"],
                "rows": [["2026-%02d" % (i + 1), 100.0 * (i + 1)] for i in range(n)]}

    def test_a_monthly_series_is_a_line(self):
        spec = X.chart_spec("sales_by_month", self.months(6))
        self.assertEqual(spec["type"], "line")
        self.assertEqual(spec["values"][0], 100.0)
        self.assertEqual(spec["label"], "Sales")

    def test_a_ranking_is_bars(self):
        result = {"columns": ["Customer", "Amount"],
                  "rows": [["A", "300.00"], ["B", "200.00"], ["C", "100.00"]]}
        self.assertEqual(X.chart_spec("top_customers", result)["type"], "bar")

    def test_a_statement_gets_no_chart(self):
        self.assertIsNone(X.chart_spec("ledger_statement", self.months(6)))

    def test_too_few_points_get_no_chart(self):
        self.assertIsNone(X.chart_spec("sales_by_month", self.months(2)))

    def test_the_chart_is_drawn_in_the_browser_and_survives_without_the_library(self):
        script = source("static", "script.js")
        self.assertIn("function drawChatCharts", script)
        self.assertIn("typeof window.Chart !== 'function'", script)
        self.assertIn("chart.umd.min.js", source("templates", "base.html"))


# ----------------------------------------- 7. learning from what went unanswered

class InsightsTests(unittest.TestCase):

    def setUp(self):
        self.tag = "zz-test-" + uuid.uuid4().hex[:8]

    def tearDown(self):
        from database.config import get_connection

        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM chat_misses WHERE question LIKE %s", (self.tag + "%",))
            cur.execute("DELETE FROM chat_feedback WHERE question LIKE %s", (self.tag + "%",))
            conn.commit()
        finally:
            conn.close()

    def test_misses_are_grouped_by_wording(self):
        from database.chat_insights_db import record_miss, top_misses

        record_miss(1, 1, self.tag + " Sales by city", "no_report_matched")
        record_miss(1, 1, self.tag + " sales BY city ", "no_report_matched")
        record_miss(1, 1, self.tag + " something else", "no_report_matched")
        mine = [m for m in top_misses(1, days=1, limit=500)
                if m["question"].startswith(self.tag)]
        self.assertEqual(mine[0]["asked"], 2)
        self.assertEqual(len(mine), 2)

    def test_misses_belong_to_their_company(self):
        from database.chat_insights_db import record_miss, top_misses

        record_miss(1, 1, self.tag + " company one only", "no_report_matched")
        others = top_misses(987654, days=1, limit=500)
        self.assertFalse(any(m["question"].startswith(self.tag) for m in others))

    def test_a_vote_is_recorded_and_counted(self):
        from database.chat_insights_db import feedback_summary, record_feedback

        before = feedback_summary(1, days=1)["down"]
        record_feedback(1, 1, self.tag + " wrong answer", "sales_total", "down")
        after = feedback_summary(1, days=1)
        self.assertEqual(after["down"], before + 1)
        self.assertTrue(any(w["question"].startswith(self.tag) for w in after["wrong"]))

    def test_only_up_or_down_is_a_vote(self):
        from database.chat_insights_db import record_feedback

        with self.assertRaises(ValueError):
            record_feedback(1, 1, self.tag + " q", "t", "meh")

    def test_recording_a_miss_never_raises(self):
        from database.chat_insights_db import record_miss

        record_miss(None, None, None, None)      # nothing to record, no error

    def test_the_insights_page_is_for_admins_and_principals(self):
        from accounting_app.chat_insights_routes import may_view_insights

        class U(object):
            def __init__(self, admin=False, principal=False):
                self.is_admin, self.is_principal = admin, principal

        self.assertTrue(may_view_insights(U(admin=True)))
        self.assertTrue(may_view_insights(U(principal=True)))
        self.assertFalse(may_view_insights(U()))

    def test_both_chat_paths_note_what_they_could_not_answer(self):
        self.assertIn("_note_if_unanswered(result, user_query, company_id)",
                      source("accounting_app", "chat_routes.py"))
        self.assertIn("record_miss(company_id, user_id, question, reason)",
                      source("accounting_app", "mobile_api.py"))
        self.assertIn('"agent_no_report"', source("accounting_app", "chat_agent_routes.py"))

    def test_every_answer_carries_the_thumbs(self):
        reply, _token = CR.answer({"title": "T", "summary": "S",
                                   "columns": ["Ledger"], "rows": [["Cash"]]},
                                  "cash", "cash_balance")
        self.assertIn("class='rv-feedback'", reply["response"])
        self.assertIn("data-tool='cash_balance'", reply["response"])


class CacheTests(unittest.TestCase):

    def test_the_browser_is_told_the_script_and_styles_changed(self):
        base = source("templates", "base.html")
        self.assertNotIn("script.js') }}?v=20260127_7", base)
        self.assertNotIn("ui.css') }}?v=20260920_1", base)


if __name__ == "__main__":
    unittest.main()
