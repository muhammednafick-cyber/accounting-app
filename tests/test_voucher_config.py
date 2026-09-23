"""Which ledgers a voucher type may use, per side.

These ran with no company open and failed on every run: configs are per
company, and the database rightly refuses a row that belongs to none. They
also left their test rows behind in the dev database each time. Both are
fixed - a real company is used, and everything written is removed.
"""
import os
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from database import get_connection
from database.voucher_config_db import (get_allowed_ledgers, get_voucher_config,
                                        save_voucher_config)

TEST_TYPES = ("TestVoucher", "TestVoucherEmpty", "TestVoucherDefault")


def _a_company_with_ledgers():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT company_id FROM ledgers GROUP BY company_id "
                    "ORDER BY COUNT(*) DESC LIMIT 1")
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _remove_test_configs():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM voucher_type_configs WHERE voucher_type IN (%s, %s, %s)",
                    TEST_TYPES)
        conn.commit()
    finally:
        conn.close()


class TestVoucherConfig(unittest.TestCase):

    def setUp(self):
        self.company_id = _a_company_with_ledgers()
        if not self.company_id:
            self.skipTest("no company with ledgers in this database")
        # Earlier runs of the old version of this file left rows behind.
        _remove_test_configs()

    def tearDown(self):
        _remove_test_configs()

    def test_save_and_get_config(self):
        save_voucher_config("TestVoucher", "Debit", ["G001", "G002"], [1, 2],
                            company_id=self.company_id)

        config = get_voucher_config("TestVoucher", "Debit",
                                    company_id=self.company_id)
        self.assertIsNotNone(config)
        self.assertEqual(set(config['allowed_groups']), {"G001", "G002"})
        self.assertEqual(set(config['allowed_sub_groups']), {1, 2})

    def test_a_config_limits_the_ledgers_offered(self):
        save_voucher_config("TestVoucherEmpty", "Debit", ["NON_EXISTENT_GROUP"], [],
                            company_id=self.company_id)
        ledgers = get_allowed_ledgers("TestVoucherEmpty", "Debit",
                                      company_id=self.company_id)
        self.assertEqual(len(ledgers), 0)

    def test_no_config_means_every_ledger(self):
        ledgers = get_allowed_ledgers("TestVoucherDefault", "Debit",
                                      company_id=self.company_id)
        self.assertGreater(len(ledgers), 0)

    def test_saving_an_empty_selection_removes_the_restriction(self):
        # An empty selection must not quietly mean "nothing is allowed".
        save_voucher_config("TestVoucher", "Debit", ["G001"], [],
                            company_id=self.company_id)
        save_voucher_config("TestVoucher", "Debit", [], [],
                            company_id=self.company_id)
        self.assertIsNone(get_voucher_config("TestVoucher", "Debit",
                                             company_id=self.company_id))


if __name__ == '__main__':
    unittest.main()
