"""Sales and Service Income in the chat: template, upload, or the voucher page.

No AI and no typing. Before this, Sales showed two buttons and then the
selector handler carried on - re-enabling the text box and posting a typing
welcome underneath them - and opening the chat with Sales already chosen never
drew the buttons at all. Service Income was typed, and only worked with AI.
"""
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def js():
    with io.open(os.path.join(ROOT, "static", "script.js"), encoding="utf-8") as f:
        return f.read()


class ExcelOnlyPanelTests(unittest.TestCase):

    def test_sales_and_service_income_are_excel_only(self):
        self.assertIn("const EXCEL_ONLY_TYPES = ['Sales', 'Purchase', 'Service Income'];", js())

    def test_the_panel_offers_exactly_the_three_ways_in(self):
        code = js()
        body = code[code.index("function showExcelOptions"):code.index("function ensureVoucherTypeSelected")]
        for label in ("'Download Template'", "'Upload Excel'", "'Go to Voucher Page'"):
            self.assertIn(label, body)
        self.assertIn("`/download_voucher_template/${encodeURIComponent(vt)}`", body)
        self.assertIn("`/voucher/${encodeURIComponent(slug)}`", body)
        self.assertIn("getElementById('vaChatFileInput')", body)

    def test_the_selector_does_not_reenable_typing_after_the_panel(self):
        code = js()
        handler = code[code.index("voucherSelect.addEventListener('change'"):]
        handler = handler[:handler.index("loadVoucherAssistantBootstrap()")]
        self.assertIn("if (isExcelOnlyType(assistantState.voucherType)) return;", handler)

    def test_opening_the_chat_draws_the_panel_instead_of_a_typing_welcome(self):
        code = js()
        opener = code[code.index("    function openGlobalChat() {"):]
        opener = opener[:opener.index("loadVoucherAssistantBootstrap()")]
        self.assertIn("if (isExcelOnlyType(assistantState.voucherType)) {", opener)
        self.assertIn("lockTypingForExcelOnly(assistantState.voucherType);", opener)

    def test_the_typed_voucher_buttons_are_hidden_for_these_types(self):
        with io.open(os.path.join(ROOT, "static", "ui.css"), encoding="utf-8") as f:
            css = f.read()
        self.assertIn(".global-chat-window.is-excel-only .rv-chat-actions", css)
        self.assertIn("win.classList.toggle('is-excel-only', isExcelOnlyType(assistantState.voucherType));", js())


class TheLinksWorkTests(unittest.TestCase):
    """Each button leads somewhere real, for both types."""

    @classmethod
    def setUpClass(cls):
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            import app as appmod
        from database.config import get_connection

        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE username = 'admin'")
            row = cur.fetchone()
            cur.execute("SELECT id FROM companies ORDER BY id LIMIT 1")
            company = cur.fetchone()
        finally:
            conn.close()
        if not row or not company:
            raise unittest.SkipTest("needs the admin user and a company")
        cls.client = appmod.app.test_client()
        with cls.client.session_transaction() as s:
            s["_user_id"] = str(row[0])
            s["_fresh"] = True
            s["company_id"] = company[0]

    def get(self, url):
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            return self.client.get(url)

    def test_both_templates_download_as_excel(self):
        for vt in ("Sales", "Service%20Income"):
            r = self.get("/download_voucher_template/" + vt)
            self.assertEqual(r.status_code, 200, vt)
            self.assertIn("spreadsheetml", r.headers.get("Content-Type", ""), vt)

    def test_both_voucher_pages_open(self):
        for slug in ("sales", "service_income"):
            self.assertEqual(self.get("/voucher/" + slug).status_code, 200, slug)


if __name__ == "__main__":
    unittest.main()
