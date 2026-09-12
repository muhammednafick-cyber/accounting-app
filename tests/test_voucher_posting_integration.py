"""Voucher entry end to end, against a real database (R1, R2, X1, X3).

These tests post into an isolated throwaway company and assert on the ledger
that results - the one thing unit tests with mocked cursors cannot check. They
need PostgreSQL; without it they skip rather than fail, so the offline suite
stays usable.
"""
import os
import sys
import unittest
import uuid

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def _database_available():
    try:
        from database.config import get_connection
        conn = get_connection()
        conn.close()
        return True
    except Exception:
        return False


AVAILABLE = _database_available()


@unittest.skipUnless(AVAILABLE, "PostgreSQL is not reachable")
class VoucherPostingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from accounting_app import create_app
        from database.company_db import create_company_profile
        from database.master_db import create_company, add_user, get_user_by_username
        from werkzeug.security import generate_password_hash

        cls.app = create_app()
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        cls.app.secret_key = "test_secret"
        cls.context = cls.app.app_context()
        cls.context.push()

        cls.username = "itest_voucher_user"
        if not get_user_by_username(cls.username):
            add_user(cls.username, "itest@example.com",
                     generate_password_hash("unused-in-these-tests"), is_admin=1)
        cls.user_id = get_user_by_username(cls.username)["id"]

        cls.company_name = f"Posting Test {uuid.uuid4().hex[:8]}"
        cls.company_id = create_company(cls.company_name)
        create_company_profile(cls.company_name, company_id=cls.company_id)

        # Posting needs a financial year covering the dates these tests use.
        from database.financial_year_db import create_fy
        create_fy("2026-2027", "2026-04-01", "2027-03-31", company_id=cls.company_id)

    @classmethod
    def tearDownClass(cls):
        from database.config import get_connection
        conn = get_connection()
        try:
            cursor = conn.cursor()
            for table in ("voucher_items", "voucher_entries", "vouchers",
                          "financial_years"):
                cursor.execute(f"DELETE FROM {table} WHERE company_id = %s",
                               (cls.company_id,))
            # Leave no throwaway company behind in the company list.
            cursor.execute("DELETE FROM companies WHERE id = %s", (cls.company_id,))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            conn.close()
        cls.context.pop()

    def setUp(self):
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True
            session["company_id"] = self.company_id
            session["company_name"] = self.company_name

    # ----------------------------------------------------------------- helpers

    def ledger_pair(self):
        """Two postable ledgers from this company's seeded chart of accounts."""
        from database.accounts_db import get_ledgers
        names = [l["ledger_name"] for l in get_ledgers(company_id=self.company_id)]
        self.assertGreaterEqual(len(names), 2,
                                "the seeded company should have a chart of accounts")
        return names[0], names[1]

    def voucher_count(self):
        from database.config import get_connection
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM vouchers WHERE company_id = %s",
                           (self.company_id,))
            return cursor.fetchone()[0]
        finally:
            conn.close()

    def journal_form(self, token, amount="125.00"):
        debit, credit = self.ledger_pair()
        return {
            "voucher_type": "Journal",
            "date": "15-06-2026",
            "narration": "integration test",
            "submission_token": token,
            "ledger_name[]": [debit, credit],
            "ledger_amount[]": [amount, amount],
            "ledger_type[]": ["Debit", "Credit"],
            "ledger_cost_center[]": ["", ""],
        }

    def post_journal(self, token, **kwargs):
        return self.client.post("/add_voucher", data=self.journal_form(token, **kwargs),
                                headers={"X-Requested-With": "XMLHttpRequest"})

    # ------------------------------------------------------------------- tests

    def test_the_entry_form_carries_a_submission_token_and_a_draft_key(self):
        response = self.client.get("/voucher/Journal")
        self.assertEqual(response.status_code, 200)
        page = response.data.decode("utf-8")
        self.assertIn('name="submission_token"', page)
        self.assertIn('data-autosave="voucher.', page)
        self.assertIn('data-validate-hook="voucherFormProblems"', page)
        self.assertIn("forms.js", page)

    def test_each_form_render_gets_its_own_token(self):
        import re
        pattern = re.compile(r'name="submission_token"[^>]*value="([0-9a-f]+)"')
        first = pattern.search(self.client.get("/voucher/Journal").data.decode())
        second = pattern.search(self.client.get("/voucher/Journal").data.decode())
        self.assertIsNotNone(first)
        self.assertNotEqual(first.group(1), second.group(1))

    def test_a_balanced_journal_posts(self):
        before = self.voucher_count()
        response = self.post_journal(uuid.uuid4().hex)
        self.assertEqual(response.status_code, 200, response.data[:400])
        body = response.get_json()
        self.assertTrue(body["success"], body)
        self.assertEqual(self.voucher_count(), before + 1)

    def test_the_same_submission_sent_twice_posts_one_voucher(self):
        token = uuid.uuid4().hex
        before = self.voucher_count()
        first = self.post_journal(token).get_json()
        second = self.post_journal(token).get_json()
        self.assertTrue(first["success"], first)
        self.assertTrue(second.get("duplicate"), second)
        self.assertEqual(self.voucher_count(), before + 1,
                         "a repeated submission must not post a second voucher")

    def test_an_unbalanced_journal_is_refused_and_posts_nothing(self):
        token = uuid.uuid4().hex
        before = self.voucher_count()
        form = self.journal_form(token)
        form["ledger_amount[]"] = ["125.00", "100.00"]
        response = self.client.post("/add_voucher", data=form,
                                    headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertGreaterEqual(response.status_code, 400)
        self.assertEqual(self.voucher_count(), before)

    def test_a_refused_submission_may_be_corrected_and_posted_with_the_same_token(self):
        token = uuid.uuid4().hex
        before = self.voucher_count()
        unbalanced = self.journal_form(token)
        unbalanced["ledger_amount[]"] = ["125.00", "100.00"]
        self.client.post("/add_voucher", data=unbalanced,
                         headers={"X-Requested-With": "XMLHttpRequest"})
        corrected = self.post_journal(token).get_json()
        self.assertTrue(corrected["success"], corrected)
        self.assertFalse(corrected.get("duplicate"))
        self.assertEqual(self.voucher_count(), before + 1)


if __name__ == "__main__":
    unittest.main()
