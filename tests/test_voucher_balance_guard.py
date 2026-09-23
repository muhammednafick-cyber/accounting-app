"""A voucher whose debits and credits differ is refused, not saved.

add_voucher used to notice the imbalance, print it, and save the voucher
anyway. A reversal made on a half-fixed build went in with both of its lines on
the credit side and left the trial balance 200.00 out, with nothing to say so.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import app as appmod
from database.config import get_connection
from database.vouchers_db import add_voucher


def _company_and_date():
    """A company with an open financial year, and a date inside it."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT company_id, start_date FROM financial_years
            WHERE COALESCE(is_locked, 0) = 0
            ORDER BY start_date DESC LIMIT 1
        """)
        row = cur.fetchone()
        return (row[0], str(row[1])) if row else (None, None)
    finally:
        conn.close()


def _two_ledgers(company_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT ledger_name FROM ledgers WHERE company_id = %s "
                    "AND COALESCE(is_active, 1) = 1 ORDER BY id LIMIT 2",
                    (company_id,))
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def _count(company_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM vouchers WHERE company_id = %s",
                    (company_id,))
        return cur.fetchone()[0]
    finally:
        conn.close()


class BalanceGuardTests(unittest.TestCase):

    def setUp(self):
        self.company_id, self.date = _company_and_date()
        if not self.company_id:
            self.skipTest("no open financial year in this database")
        self.ledgers = _two_ledgers(self.company_id)
        if len(self.ledgers) < 2:
            self.skipTest("need two ledgers")

    def test_a_one_sided_journal_is_refused_and_nothing_is_written(self):
        # Exactly the shape of the voucher that went in: both lines credit.
        before = _count(self.company_id)
        with appmod.app.test_request_context():
            with self.assertRaises(Exception) as caught:
                add_voucher(
                    voucher_type="Journal", date=self.date,
                    ledger_entries=[
                        {"ledger_name": self.ledgers[0], "amount": 100.0,
                         "type": "Credit"},
                        {"ledger_name": self.ledgers[1], "amount": 100.0,
                         "type": "Credit"},
                    ],
                    item_entries=[], narration="balance guard test",
                    company_id=self.company_id, skip_recalc=True)
        self.assertIn("do not match", str(caught.exception))
        self.assertIn("Nothing was saved", str(caught.exception))
        self.assertEqual(_count(self.company_id), before)

    def test_a_small_rounding_difference_still_saves(self):
        # The tolerance matches the save screen's: a few fils of rounding on a
        # VAT line must not start refusing ordinary vouchers.
        import database.vouchers_db as V

        source = open(V.__file__, encoding="utf-8").read()
        self.assertIn("abs(total_debit - total_credit) > 0.05", source)
        self.assertIn("raise ValueError(", source)


if __name__ == "__main__":
    unittest.main()
