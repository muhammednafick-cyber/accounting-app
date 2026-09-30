"""The General Chat over Telegram: webhook, linking, answering.

Telegram is never called: send_text / send_document are replaced by
recorders, the webhook's thread runs inline, and the admin page's calls to
the Bot API are mocked.
"""
import contextlib
import io
import json
import os
import random
import sys
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

with contextlib.redirect_stdout(io.StringIO()):
    import app as appmod
from accounting_app import telegram_routes as T
from database import whatsapp_db as db
from database.config import get_connection
from database.master_db import get_system_setting, set_system_setting

SECRET = "tg-test-secret"
TEST_SETTINGS = {"enabled": "1", "bot_token": "123:test", "webhook_secret": SECRET,
                 "bot_username": "ProdataTestBot", "ai_enabled": "0"}


def _admin_id():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE username = 'admin'")
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


class PureTests(unittest.TestCase):

    def test_commands_become_words(self):
        self.assertEqual(T.normalise("/start LINK123456"), "LINK 123456")
        self.assertEqual(T.normalise("/start link_123456"), "LINK 123456")
        self.assertIsNone(T.normalise("/start"))
        self.assertEqual(T.normalise("/pdf@ProdataBot"), "pdf")
        self.assertEqual(T.normalise("/company 2"), "company 2")
        self.assertEqual(T.normalise("cash balance"), "cash balance")

    def test_markup_becomes_safe_html(self):
        self.assertEqual(T.to_telegram_html("Total *5 < 6* & _note_"),
                         "Total <b>5 &lt; 6</b> &amp; <i>note</i>")
        # An underscore inside a name is not italics.
        self.assertEqual(T.to_telegram_html("ledger_name_x"), "ledger_name_x")


class WebhookTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.user_id = _admin_id()
        if not cls.user_id:
            raise unittest.SkipTest("needs the admin user")
        with contextlib.redirect_stdout(io.StringIO()):
            db.init_whatsapp_tables()
        cls.saved = {k: get_system_setting(T.SETTING_KEYS[k]) for k in T.SETTING_KEYS}
        for k, v in TEST_SETTINGS.items():
            set_system_setting(T.SETTING_KEYS[k], v)
        cls.client = appmod.app.test_client()

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.saved.items():
            set_system_setting(T.SETTING_KEYS[k], v if v is not None else "")

    def setUp(self):
        self.chat_id = random.randint(10 ** 9, 10 ** 10)
        self.ext_id = f"tg:{self.chat_id}"
        self.sent, self.docs = [], []
        patches = [
            mock.patch.object(T, "send_text", lambda to, text: self.sent.append((to, text)) or True),
            mock.patch.object(T, "send_document",
                              lambda to, data, name, mime: self.docs.append((to, name, data[:4])) or True),
            mock.patch.object(T, "dispatch", lambda app, message: T._answer_in_context(app, message)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(db.unlink_number, self.ext_id)
        self.addCleanup(db.unlink_user, self.user_id, "telegram")

    def post(self, text, secret=SECRET, chat_type="private"):
        body = {"update_id": random.randint(1, 10 ** 12), "message": {
            "message_id": 1, "chat": {"id": self.chat_id, "type": chat_type},
            "from": {"id": self.chat_id}, "text": text}}
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["X-Telegram-Bot-Api-Secret-Token"] = secret
        with contextlib.redirect_stdout(io.StringIO()):
            return self.client.post("/telegram/webhook", data=json.dumps(body), headers=headers)

    def last(self):
        return self.sent[-1][1] if self.sent else ""

    def link(self):
        code = db.start_link(self.user_id, 1, channel="telegram")
        self.post(f"/start LINK{code}")        # the button's deep link
        self.assertIn("Linked", self.last())

    def test_the_secret_is_required(self):
        self.assertEqual(self.post("cash balance", secret=None).status_code, 403)
        self.assertEqual(self.post("cash balance", secret="wrong").status_code, 403)
        self.assertEqual(self.sent, [])

    def test_group_chats_are_ignored(self):
        self.assertEqual(self.post("cash balance", chat_type="group").status_code, 200)
        self.assertEqual(self.sent, [])

    def test_a_bare_start_explains_linking(self):
        self.post("/start")
        self.assertIn("not linked", self.last())

    def test_linking_by_button_then_asking(self):
        self.link()
        self.post("cash balance")
        self.assertIn("cash balance", self.last().lower())
        self.assertEqual(self.sent[-1][0], self.ext_id)

    def test_pdf_by_command(self):
        self.link()
        self.post("trial balance")
        self.post("/pdf")
        self.assertEqual(len(self.docs), 1)
        self.assertEqual(self.docs[0][2], b"%PDF")

    def test_a_whatsapp_code_does_not_link_telegram(self):
        code = db.start_link(self.user_id, 1, channel="whatsapp")
        self.addCleanup(db.unlink_user, self.user_id, "whatsapp")
        self.post(f"LINK {code}")
        self.assertIn("not valid", self.last())

    def test_linking_telegram_keeps_the_whatsapp_link(self):
        wa = "9715" + str(random.randint(10 ** 7, 10 ** 8))
        self.addCleanup(db.unlink_number, wa)
        code = db.start_link(self.user_id, 1, channel="whatsapp")
        self.assertTrue(db.complete_link(code, wa, channel="whatsapp"))
        self.link()
        self.assertIsNotNone(db.link_for_number(wa))
        self.assertIsNotNone(db.link_for_number(self.ext_id))

    def test_unlink_by_command(self):
        self.link()
        self.post("/unlink")
        self.assertIn("unlinked", self.last())
        self.assertIsNone(db.link_for_number(self.ext_id))


class AdminTests(unittest.TestCase):

    def test_saving_a_token_registers_the_webhook_with_the_secret(self):
        user_id = _admin_id()
        if not user_id:
            self.skipTest("needs the admin user")
        saved = {k: get_system_setting(T.SETTING_KEYS[k]) for k in T.SETTING_KEYS}
        self.addCleanup(lambda: [set_system_setting(T.SETTING_KEYS[k], v or "")
                                 for k, v in saved.items()])
        calls = []

        class Reply:
            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        def fake_get(url, **kwargs):
            calls.append(("GET", url.rsplit("/", 1)[-1], kwargs))
            if url.endswith("getMe"):
                return Reply({"ok": True, "result": {"username": "ProdataTestBot"}})
            return Reply({"ok": True, "result": {}})

        def fake_post(url, **kwargs):
            calls.append(("POST", url.rsplit("/", 1)[-1], kwargs))
            return Reply({"ok": True, "result": True})

        appmod.app.config["WTF_CSRF_ENABLED"] = False
        self.addCleanup(appmod.app.config.__setitem__, "WTF_CSRF_ENABLED", True)
        client = appmod.app.test_client()
        # The session must belong to the https host the form is posted to.
        with client.session_transaction(base_url="https://account.example.com") as s:
            s["_user_id"] = str(user_id)
            s["_fresh"] = True
            s["company_id"] = 1
        with mock.patch.object(T.requests, "get", fake_get), \
                mock.patch.object(T.requests, "post", fake_post), \
                contextlib.redirect_stdout(io.StringIO()):
            client.post("/admin/telegram", data={"action": "save", "bot_token": "999:abc",
                                                 "enabled": "1"},
                        base_url="https://account.example.com")
        set_webhook = [c for c in calls if c[1] == "setWebhook"]
        self.assertEqual(len(set_webhook), 1)
        body = set_webhook[0][2]["json"]
        self.assertEqual(body["url"], "https://account.example.com/telegram/webhook")
        self.assertEqual(body["secret_token"], get_system_setting("telegram_webhook_secret"))
        self.assertEqual(get_system_setting("telegram_bot_username"), "ProdataTestBot")

    def test_pages_render(self):
        user_id = _admin_id()
        if not user_id:
            self.skipTest("needs the admin user")
        client = appmod.app.test_client()
        with client.session_transaction() as s:
            s["_user_id"] = str(user_id)
            s["_fresh"] = True
            s["company_id"] = 1
        with mock.patch.object(T.requests, "get", side_effect=Exception("offline")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(client.get("/settings/telegram").status_code, 200)
            self.assertEqual(client.get("/admin/telegram").status_code, 200)


if __name__ == "__main__":
    unittest.main()
