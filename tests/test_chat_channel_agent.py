"""The Agent in Telegram / WhatsApp - used only when it is needed.

When the free reports cannot answer in one go (near misses, or nothing close)
the chat offers the choices and the Agent; the person replies with a number
or AGENT. An administrator can make that automatic, or turn the Agent off.
/agent <question> asks for it directly. The engine and the Agent are mocked.
"""
import contextlib
import io
import json
import os
import random
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

with contextlib.redirect_stdout(io.StringIO()):
    import app as appmod
from accounting_app import telegram_routes as T
from accounting_app.whatsapp_text import to_whatsapp
from database import whatsapp_db as db
from database.config import get_connection
from database.master_db import get_system_setting, set_system_setting

SECRET = "tg-agent-secret"
BASE_SETTINGS = {"enabled": "1", "bot_token": "123:test", "webhook_secret": SECRET,
                 "bot_username": "ProdataTestBot", "ai_enabled": "1", "agent_mode": "offer"}
SUGGESTION = {"intent": "suggestion",
              "response": "I'm not certain which of these you meant:<br>"
                          "<button class='rv-pick'>Slow moving stock</button>"
                          "<button class='rv-pick'>Closing stock value</button>",
              "data": {"options": ["Slow moving stock", "Closing stock value"]}}
AGENT_REPLY = {"intent": "agent",
               "response": "<b>Closing stock</b><br>value<div class='rv-agent-say'>"
                           "Stock is worth 651.75, of which 101.75 has not moved in "
                           "six months.</div>"}


class FormattingTests(unittest.TestCase):

    def test_choices_are_separate_lines(self):
        text, _ = to_whatsapp(SUGGESTION["response"])
        self.assertIn("• Slow moving stock\n• Closing stock value", text)

    def test_the_agents_summary_comes_first(self):
        text, _ = to_whatsapp(AGENT_REPLY["response"])
        self.assertTrue(text.startswith("Stock is worth 651.75"))
        self.assertIn("_Written by AI from the figures below._", text)

    def test_a_bold_heading_keeps_its_line_break(self):
        text, _ = to_whatsapp("<b>Inventory Ageing</b><br><b>12</b> item(s) aged.")
        self.assertIn("*Inventory Ageing*\n*12* item(s)", text)

    def test_wide_tables_keep_first_and_last_columns(self):
        head = "".join(f"<th>C{i}</th>" for i in range(10))
        row = "".join(f"<td>{i}</td>" for i in range(10))
        text, _ = to_whatsapp(f"<table><thead><tr>{head}</tr></thead><tr>{row}</tr></table>")
        self.assertIn("• *0* — C1: 1 · C2: 2 · … · C8: 8 · C9: 9", text)


class _AgentChatBase(unittest.TestCase):
    """Shared set-up: a linked Telegram chat, the engine and the Agent mocked."""

    @classmethod
    def setUpClass(cls):
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE username = 'admin'")
            row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            raise unittest.SkipTest("needs the admin user")
        cls.user_id = row[0]
        with contextlib.redirect_stdout(io.StringIO()):
            db.init_whatsapp_tables()
        cls.saved = {k: get_system_setting(T.SETTING_KEYS[k]) for k in T.SETTING_KEYS}
        cls.client = appmod.app.test_client()

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.saved.items():
            set_system_setting(T.SETTING_KEYS[k], v if v is not None else "")

    def setUp(self):
        for k, v in BASE_SETTINGS.items():
            set_system_setting(T.SETTING_KEYS[k], v)
        self.chat_id = random.randint(10 ** 9, 10 ** 10)
        self.ext_id = f"tg:{self.chat_id}"
        self.sent, self.asked, self.agent_questions = [], [], []

        def fake_query(question, company_id, **kwargs):
            self.asked.append(question)
            return SUGGESTION if "stock worth" in question else {"intent": "x", "response": "ok"}

        def fake_agent(question, company_id, **kwargs):
            self.agent_questions.append(question)
            return AGENT_REPLY

        patches = [
            mock.patch.object(T, "send_text", lambda to, text: self.sent.append(text) or True),
            mock.patch.object(T, "dispatch", lambda app, message: T._answer_in_context(app, message)),
            mock.patch("accounting_app.chatbot_service.process_chat_query", fake_query),
            mock.patch("accounting_app.chat_agent.run", fake_agent),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(db.unlink_number, self.ext_id)
        self.addCleanup(db.unlink_user, self.user_id, "telegram")
        code = db.start_link(self.user_id, 1, channel="telegram")
        self.post(f"/start LINK{code}")

    def post(self, text):
        body = {"update_id": random.randint(1, 10 ** 12), "message": {
            "message_id": 1, "chat": {"id": self.chat_id, "type": "private"}, "text": text}}
        with contextlib.redirect_stdout(io.StringIO()):
            self.client.post("/telegram/webhook", data=json.dumps(body),
                             headers={"Content-Type": "application/json",
                                      "X-Telegram-Bot-Api-Secret-Token": SECRET})



class AgentFlowTests(_AgentChatBase):

    QUESTION = "what is the stock worth and how much is non moving"

    def test_a_stuck_question_offers_numbers_and_the_agent(self):
        self.post(self.QUESTION)
        self.assertIn("*1.* Slow moving stock", self.sent[-1])
        self.assertIn("*2.* Closing stock value", self.sent[-1])
        self.assertIn("*AGENT*", self.sent[-1])
        self.assertEqual(self.agent_questions, [])

    def test_replying_a_number_asks_that_report(self):
        self.post(self.QUESTION)
        self.post("2")
        self.assertEqual(self.asked[-1], "Closing stock value")

    def test_replying_agent_runs_the_whole_question(self):
        self.post(self.QUESTION)
        self.post("AGENT")
        self.assertEqual(self.agent_questions, [self.QUESTION])
        self.assertIn("Working on it", self.sent[-2])
        self.assertTrue(self.sent[-1].startswith("Stock is worth 651.75"))

    def test_the_offer_lapses_after_another_question(self):
        self.post(self.QUESTION)
        self.post("cash balance")
        self.post("AGENT")
        self.assertEqual(self.agent_questions, [])

    def test_automatic_mode_runs_the_agent_straight_away(self):
        set_system_setting(T.SETTING_KEYS["agent_mode"], "auto")
        self.post(self.QUESTION)
        self.assertEqual(self.agent_questions, [self.QUESTION])

    def test_agent_off_only_lists_the_reports(self):
        set_system_setting(T.SETTING_KEYS["agent_mode"], "off")
        self.post(self.QUESTION)
        self.assertIn("*1.* Slow moving stock", self.sent[-1])
        self.assertNotIn("AGENT", self.sent[-1])
        self.post("/agent " + self.QUESTION)
        self.assertEqual(self.agent_questions, [])
        self.assertIn("switched off", self.sent[-1])

    def test_ai_off_means_no_agent(self):
        set_system_setting(T.SETTING_KEYS["ai_enabled"], "0")
        self.post(self.QUESTION)
        self.assertNotIn("AGENT", self.sent[-1])

    def test_agent_command_asks_directly(self):
        self.post("/agent how is my stock")
        self.assertEqual(self.agent_questions, ["how is my stock"])

    def test_simple_questions_never_see_the_agent(self):
        self.post("cash balance")
        self.assertEqual(self.sent[-1], "ok")
        self.assertEqual(self.agent_questions, [])


class AgentConversationTests(_AgentChatBase):
    """After an Agent answer, follow-ups carry on with the Agent and its
    history; a clearly new question goes back to the free reports."""

    def setUp(self):
        super().setUp()
        self.histories = []

        def fake_agent(question, company_id, history=None, **kwargs):
            self.agent_questions.append(question)
            self.histories.append(list(history or []))
            reply = dict(AGENT_REPLY)
            reply["data"] = {"memory": f"Answered: {question}", "tools_used": ["closing_stock_value"]}
            return reply
        p = mock.patch("accounting_app.chat_agent.run", fake_agent)
        p.start()
        self.addCleanup(p.stop)
        self.post("/agent what is the stock worth")

    def test_a_follow_up_goes_to_the_agent_with_the_conversation(self):
        self.post("Give it as summary")
        self.assertEqual(self.agent_questions[-1], "Give it as summary")
        self.assertEqual(self.histories[-1], [
            {"role": "user", "content": "what is the stock worth"},
            {"role": "assistant", "content": "Answered: what is the stock worth"}])
        self.post("what about last year")
        self.assertEqual(len(self.histories[-1]), 4)

    def test_the_answer_says_follow_ups_work(self):
        self.assertIn("Ask a follow-up", self.sent[-1])

    def test_the_answer_is_the_summary_not_the_tables(self):
        self.assertTrue(self.sent[-1].startswith("Stock is worth 651.75"))
        self.assertNotIn("Closing stock*", self.sent[-1])
        self.assertIn("Written by AI from your reports (closing stock value)", self.sent[-1])

    def test_a_new_question_goes_to_the_reports_and_ends_it(self):
        self.post("cash balance")
        self.assertEqual(self.sent[-1], "ok")
        self.post("what about last year")
        self.assertEqual(self.agent_questions, ["what is the stock worth"])
        self.assertEqual(self.asked[-1], "what about last year")

    def test_reset_ends_it(self):
        self.post("/reset")
        self.post("give it as summary")
        self.assertEqual(self.agent_questions, ["what is the stock worth"])

    def test_it_ends_after_a_while(self):
        state = db.link_for_number(self.ext_id)["agent_state"]
        state["at"] -= 16 * 60
        db.set_agent_state(self.ext_id, state)
        self.post("give it as summary")
        self.assertEqual(self.agent_questions, ["what is the stock worth"])


if __name__ == "__main__":
    unittest.main()
