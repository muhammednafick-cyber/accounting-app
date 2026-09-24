"""Spreadsheets built from uploaded invoices.

They were written under static/generated, which nginx serves to anyone with
the link and no sign-in - and the folder had been created locked on the
server, so nginx could not read it either: every "Download Excel for Import"
answered 403, "File wasn't available on site". They now live outside static/
and come back only through a signed-in route, to the company that made them.
"""
import contextlib
import io
import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

with contextlib.redirect_stdout(io.StringIO()):
    import app as appmod
from accounting_app import chat_routes as CR
from database.config import get_connection


def _admin_id():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE username = 'admin'")
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


class GeneratedFileTests(unittest.TestCase):

    def setUp(self):
        self.admin = _admin_id()
        if not self.admin:
            self.skipTest("needs the admin user")
        self.name = "parsed_invoice_1_%s.xlsx" % uuid.uuid4().hex
        self.path = os.path.join(CR.GEN_DIR, self.name)
        with open(self.path, "wb") as f:
            f.write(b"PK-test-sheet")

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def client(self, company_id=None, signed_in=True):
        c = appmod.app.test_client()
        if signed_in:
            with c.session_transaction() as s:
                s["_user_id"] = str(self.admin)
                s["_fresh"] = True
                if company_id is not None:
                    s["company_id"] = company_id
        return c

    def test_the_files_are_not_under_static(self):
        # Anything under static/ is handed out by nginx without a login.
        static = os.path.join(os.path.dirname(os.path.dirname(CR.__file__)), "static")
        self.assertFalse(os.path.abspath(CR.GEN_DIR).startswith(os.path.abspath(static)))

    def test_its_own_company_gets_the_file(self):
        r = self.client(company_id=1).get("/generated/" + self.name)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_data(), b"PK-test-sheet")
        self.assertIn("attachment", r.headers.get("Content-Disposition", ""))

    def test_another_company_gets_nothing(self):
        r = self.client(company_id=2).get("/generated/" + self.name)
        self.assertEqual(r.status_code, 404)

    def test_nobody_signed_in_gets_nothing(self):
        r = self.client(signed_in=False).get("/generated/" + self.name)
        self.assertIn(r.status_code, (302, 401))

    def test_only_its_own_kind_of_name_is_served(self):
        c = self.client(company_id=1)
        for bad in ("parsed_invoice_%s.xlsx" % uuid.uuid4().hex,   # old, no company
                    "..%2F..%2F.env", "app.py",
                    "parsed_invoice_1_%s.xlsx" % uuid.uuid4().hex):  # right shape, no file
            self.assertEqual(c.get("/generated/" + bad).status_code, 404, bad)

    def test_the_invoice_reader_links_to_the_signed_in_route(self):
        import accounting_app.ai_invoice_services as AI

        real = (AI.extract_invoice_data_vision, AI.generate_purchase_excel)
        AI.extract_invoice_data_vision = lambda *a, **k: {"items": [{"description": "x"}]}
        AI.generate_purchase_excel = lambda *a, **k: io.BytesIO(b"PK-sheet")
        try:
            out = CR._process_invoice(b"pdf", "inv.pdf", "Purchase", 7)
        finally:
            AI.extract_invoice_data_vision, AI.generate_purchase_excel = real
        self.assertTrue(out["download_url"].startswith("/generated/parsed_invoice_7_"))
        made = os.path.join(CR.GEN_DIR, out["download_url"].rsplit("/", 1)[1])
        self.assertTrue(os.path.exists(made))
        os.remove(made)


if __name__ == "__main__":
    unittest.main()
