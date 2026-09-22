"""What the phone app can now do: the agent, and saving a file.

The app holds a bearer token and cannot put a header on a link, so downloads
go through a one-time ticket. These check the ticket behaves like one.
"""
import os
import sys
import time
import unittest
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import app as appmod
from accounting_app import mobile_api
from database.app_state_db import chat_export_load, chat_export_save


def park_a_result():
    """A chat result sitting in the store, as an answer leaves behind."""
    token = uuid.uuid4().hex
    chat_export_save(token, {"columns": ["Ledger", "Amount"],
                             "rows": [["Cash", "100.00"]],
                             "title": "Cash Balance"})
    return token


def issue_ticket(result_token, fmt="xlsx", ttl=None):
    ticket = uuid.uuid4().hex
    chat_export_save("ticket:" + ticket, {
        "result_token": result_token,
        "format": fmt,
        "user_id": 1,
        "company_id": 1,
        "expires_at": time.time() + (mobile_api.TICKET_TTL_SECONDS if ttl is None
                                     else ttl),
    })
    return ticket


class DownloadTicketTests(unittest.TestCase):

    def setUp(self):
        self.client = appmod.app.test_client()

    def test_a_ticket_downloads_the_file(self):
        ticket = issue_ticket(park_a_result(), "csv")
        response = self.client.get("/api/mobile/export?ticket=" + ticket)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Cash", response.get_data(as_text=True))

    def test_a_ticket_works_once(self):
        # A download URL goes to the phone's browser and lands in its history.
        # One that still works tomorrow is a copy of the data left lying about.
        ticket = issue_ticket(park_a_result())
        first = self.client.get("/api/mobile/export?ticket=" + ticket)
        second = self.client.get("/api/mobile/export?ticket=" + ticket)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 404)

    def test_an_expired_ticket_is_refused(self):
        ticket = issue_ticket(park_a_result(), ttl=-1)
        response = self.client.get("/api/mobile/export?ticket=" + ticket)
        self.assertEqual(response.status_code, 404)

    def test_an_unknown_ticket_is_refused(self):
        response = self.client.get("/api/mobile/export?ticket=" + uuid.uuid4().hex)
        self.assertEqual(response.status_code, 404)

    def test_no_ticket_is_refused(self):
        self.assertEqual(self.client.get("/api/mobile/export").status_code, 400)

    def test_issuing_a_ticket_needs_a_signed_in_app(self):
        # The ticket route is the one that needs the bearer token; the download
        # itself cannot have one.
        response = self.client.post("/api/mobile/export_ticket",
                                    json={"token": park_a_result()})
        self.assertIn(response.status_code, (401, 403))

    def test_the_ticket_carries_no_sign_in_credential(self):
        result_token = park_a_result()
        ticket = issue_ticket(result_token)
        stored = chat_export_load("ticket:" + ticket)
        self.assertEqual(stored["result_token"], result_token)
        for field in ("token", "bearer", "password", "api_key"):
            self.assertNotIn(field, stored)


class MobileAgentTests(unittest.TestCase):
    """The phone reaches the same second assistant the browser does."""

    def test_the_chat_endpoint_routes_the_agent_flag(self):
        import io

        with io.open(os.path.join(os.path.dirname(__file__), '..',
                                  'accounting_app', 'mobile_api.py'),
                     encoding='utf-8') as f:
            source = f.read()
        self.assertIn('data.get("agent")', source)
        self.assertIn("from . import chat_agent", source)

    def test_the_agent_needs_ai_to_be_on(self):
        import io

        with io.open(os.path.join(os.path.dirname(__file__), '..',
                                  'accounting_app', 'mobile_api.py'),
                     encoding='utf-8') as f:
            source = f.read()
        self.assertIn('bool(data.get("agent")) and ai_enabled', source)


class AppBundleTests(unittest.TestCase):
    """The shipped web bundle has to match what the server now offers."""

    def bundle(self, name):
        import io

        path = os.path.join(os.path.dirname(__file__), '..',
                            'android-shareholder', 'www', name)
        with io.open(path, encoding='utf-8') as f:
            return f.read()

    def test_the_app_has_an_agent_switch(self):
        self.assertIn('id="agentToggle"', self.bundle('index.html'))
        self.assertIn("agent: el('agentToggle')", self.bundle('app.js'))

    def test_a_proposal_link_is_pointed_at_the_server(self):
        # The voucher screen belongs to the web app. Left relative, the link
        # would resolve against the app bundle and go nowhere.
        source = self.bundle('app.js')
        self.assertIn("a[href^=\"/voucher/\"], a[href^=\"/settings/\"]", source)
        self.assertIn("session.server + link.getAttribute('href')", source)
        self.assertIn("openOutside(ext.dataset.external)", source)

    def test_downloads_are_offered_rather_than_stripped(self):
        source = self.bundle('app.js')
        self.assertIn('/api/mobile/export_ticket', source)
        self.assertIn("window.open(url, '_system')", source)
        # The old build deleted every download link it was sent.
        self.assertNotIn("querySelectorAll('.rv-dl, .rv-alt, a[href^=\"/export\"]')",
                         source)


if __name__ == "__main__":
    unittest.main()
