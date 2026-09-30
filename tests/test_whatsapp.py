"""The General Chat over WhatsApp: webhook, linking, answering.

Meta is never called: send_text / send_document are replaced by recorders,
and the webhook's background thread runs inline.
"""
import contextlib
import hashlib
import hmac
import io
import json
import os
import sys
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

with contextlib.redirect_stdout(io.StringIO()):
    import app as appmod
from accounting_app import whatsapp_routes as W
from accounting_app.whatsapp_text import to_whatsapp
from database import whatsapp_db as db
from database.config import get_connection
from database.master_db import get_system_setting, set_system_setting

SECRET = "test-app-secret"
NUMBER_ID = "111222333"
TEST_SETTINGS = {
    "enabled": "1", "phone_number_id": NUMBER_ID, "access_token": "test-token",
    "app_secret": SECRET, "verify_token": "verify-me", "ai_enabled": "0",
    "business_number": "971500000000",
}


class TextTests(unittest.TestCase):

    def test_tables_become_lines_and_links_go(self):
        html = ("Total is <b>5.00</b>.<br><div class='rv-table-wrap'><table class='rv-table'>"
                "<thead><tr><th>Ledger</th><th>Balance</th></tr></thead><tbody>"
                "<tr><td><button class='rv-ask'>Cash</button></td><td>5.00</td></tr>"
                "</tbody></table></div><br>"
                "<a href='/export_chat_result?token=" + "a" * 32 + "'>Download Excel</a> "
                "<small class='rv-alt'>or <a href='/x'>CSV</a></small>"
                "<small class='rv-src'>Computed from your data</small>"
                "<div class='rv-feedback'><span>Was this right?</span><button>&#128077;</button></div>"
                "<div class='rv-followups'><button>What about last year</button></div>")
        text, token = to_whatsapp(html)
        self.assertEqual(token, "a" * 32)
        self.assertIn("Total is *5.00*.", text)
        self.assertIn("• *Cash* — Balance: 5.00", text)
        for gone in ("Download", "CSV", "Computed", "Was this right", "<"):
            self.assertNotIn(gone, text)
        self.assertIn("_You can also ask:_ What about last year", text)
        self.assertIn("Reply *PDF* or *EXCEL*", text)

    def test_long_tables_are_cut_short_with_a_pointer(self):
        rows = "".join(f"<tr><td>L{i}</td><td>{i}</td></tr>" for i in range(30))
        text, _ = to_whatsapp(f"<table><thead><tr><th>A</th><th>B</th></tr></thead>{rows}</table>")
        self.assertIn("…and 18 more", text)

    def test_never_over_the_whatsapp_limit(self):
        text, _ = to_whatsapp("word " * 3000)
        self.assertLessEqual(len(text), 4096)


class WebhookTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE username = 'admin'")
            user = cur.fetchone()
        finally:
            conn.close()
        if not user:
            raise unittest.SkipTest("needs the admin user")
        cls.user_id = user[0]
        with contextlib.redirect_stdout(io.StringIO()):
            db.init_whatsapp_tables()
        cls.saved = {k: get_system_setting(W.SETTING_KEYS[k]) for k in W.SETTING_KEYS}
        for k, v in TEST_SETTINGS.items():
            set_system_setting(W.SETTING_KEYS[k], v)
        cls.client = appmod.app.test_client()

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.saved.items():
            set_system_setting(W.SETTING_KEYS[k], v if v is not None else "")

    def setUp(self):
        self.wa_id = "9715" + uuid.uuid4().hex[:8].translate(str.maketrans("abcdef", "123456"))
        self.sent, self.docs = [], []
        patches = [
            mock.patch.object(W, "send_text", lambda to, text: self.sent.append((to, text)) or True),
            mock.patch.object(W, "send_document",
                              lambda to, data, name, mime: self.docs.append((to, name, data[:4])) or True),
            mock.patch.object(W, "dispatch", lambda app, message: W._answer_in_context(app, message)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(db.unlink_number, self.wa_id)
        self.addCleanup(db.unlink_user, self.user_id)

    def post(self, text, message_id=None, signed=True, number_id=NUMBER_ID):
        body = json.dumps({"entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": number_id},
            "messages": [{"from": self.wa_id, "id": message_id or uuid.uuid4().hex,
                          "type": "text", "text": {"body": text}}]}}]}]}).encode()
        headers = {"Content-Type": "application/json"}
        if signed:
            headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(
                SECRET.encode(), body, hashlib.sha256).hexdigest()
        with contextlib.redirect_stdout(io.StringIO()):
            return self.client.post("/whatsapp/webhook", data=body, headers=headers)

    def last(self):
        return self.sent[-1][1] if self.sent else ""

    def link(self):
        code = db.start_link(self.user_id, 1)
        self.post(f"LINK {code}")
        self.assertIn("Linked", self.last())

    # -- Meta's handshake and signatures
    def test_verification_handshake(self):
        ok = self.client.get("/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=42")
        self.assertEqual((ok.status_code, ok.get_data(as_text=True)), (200, "42"))
        bad = self.client.get("/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=42")
        self.assertEqual(bad.status_code, 403)

    def test_unsigned_deliveries_are_refused(self):
        self.assertEqual(self.post("cash balance", signed=False).status_code, 403)
        self.assertEqual(self.sent, [])

    def test_another_numbers_deliveries_are_ignored(self):
        self.assertEqual(self.post("cash balance", number_id="999").status_code, 200)
        self.assertEqual(self.sent, [])

    # -- linking
    def test_an_unlinked_number_is_told_how_to_link(self):
        self.post("cash balance")
        self.assertIn("not linked", self.last())

    def test_a_wrong_code_does_not_link(self):
        db.start_link(self.user_id, 1)
        self.post("LINK 000000")
        self.assertIn("not valid", self.last())
        self.assertIsNone(db.link_for_number(self.wa_id))

    def test_linking_then_asking(self):
        self.link()
        self.post("cash balance")
        self.assertIn("cash balance", self.last().lower())
        self.assertNotIn("<", self.last())

    def test_a_table_answer_can_be_sent_as_a_pdf(self):
        self.link()
        self.post("trial balance")
        self.assertIn("Reply *PDF*", self.last())
        self.post("PDF")
        self.assertEqual(len(self.docs), 1)
        self.assertTrue(self.docs[0][1].endswith(".pdf"))
        self.assertEqual(self.docs[0][2], b"%PDF")

    def test_a_redelivered_message_is_answered_once(self):
        self.link()
        before = len(self.sent)
        mid = uuid.uuid4().hex
        self.post("cash balance", message_id=mid)
        self.post("cash balance", message_id=mid)
        self.assertEqual(len(self.sent) - before, 1)

    def test_company_list_and_unlink(self):
        self.link()
        self.post("COMPANY")
        self.assertIn("Your companies", self.last())
        self.post("UNLINK")
        self.assertIn("unlinked", self.last())
        self.post("cash balance")
        self.assertIn("not linked", self.last())

    def test_answers_carry_the_linked_users_permissions(self):
        seen = {}
        from accounting_app import chat_permissions

        def fake_query(question, company_id, **kwargs):
            seen["user"] = chat_permissions._user()
            seen["company"] = company_id
            return {"intent": "x", "response": "ok"}
        self.link()
        with mock.patch("accounting_app.chatbot_service.process_chat_query", fake_query):
            self.post("anything at all")
        self.assertEqual(getattr(seen["user"], "id", None), self.user_id)
        self.assertEqual(seen["company"], 1)


class PagesTests(unittest.TestCase):

    def test_pages_render_for_a_signed_in_admin(self):
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE username = 'admin'")
            user = cur.fetchone()
        finally:
            conn.close()
        if not user:
            self.skipTest("needs the admin user")
        client = appmod.app.test_client()
        with client.session_transaction() as s:
            s["_user_id"] = str(user[0])
            s["_fresh"] = True
            s["company_id"] = 1
        with contextlib.redirect_stdout(io.StringIO()):
            link_page = client.get("/settings/whatsapp")
            admin_page = client.get("/admin/whatsapp")
        self.assertEqual(link_page.status_code, 200)
        self.assertEqual(admin_page.status_code, 200)
        self.assertIn(b"/whatsapp/webhook", admin_page.data)


if __name__ == "__main__":
    unittest.main()
