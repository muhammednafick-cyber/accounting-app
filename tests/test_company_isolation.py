"""Each client's books are theirs alone.

Everything lives in one database, told apart by company_id. Ledger names and
voucher numbers repeat from one company to the next - two clients both have
"Cash", both have FY26-JOU-000001 - so a single query that forgets the company
quietly mixes their data. These pin the places that once did.
"""
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def source(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class CreditDaysLookupTests(unittest.TestCase):

    def test_saving_a_voucher_reads_credit_days_from_this_company_only(self):
        # It used to look the party up by name in every company, so a voucher
        # could take another client's credit days and due date.
        code = source("accounting_app", "voucher_routes.py")
        query = re.search(r'SELECT credit_days FROM ledgers WHERE[^"]*"[^"]*"', code)
        self.assertIsNotNone(query)
        self.assertIn("company_id = ?", query.group(0))
        self.assertIn("[get_current_company_id()] + list(candidates)", code)

    def test_an_import_with_no_company_does_not_look_across_companies(self):
        code = source("accounting_app", "import_routes", "queue_routes.py")
        self.assertNotIn(
            'SELECT credit_days FROM ledgers WHERE ledger_name = ? LIMIT 1', code)


class GateTests(unittest.TestCase):

    def test_opening_a_company_checks_the_user_is_assigned_to_it(self):
        code = source("accounting_app", "company_routes.py")
        body = code[code.index("def select_company"):]
        body = body[:body.index("session['company_id'] = company_id")]
        self.assertIn("master_get_user_companies(current_user.id)", body)
        self.assertIn("if not allowed:", body)


if __name__ == "__main__":
    unittest.main()
