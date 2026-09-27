"""The Voucher Configuration on agent proposals and the chatbot's vouchers.

  * a proposal was filed with any ledger, and Approve posted it straight
    through add_voucher - past the configuration and the built-in rules;
  * the chatbot's typed Expense sends its VAT as its own line, and that line
    was refused once the Expense Debit side was configured.
"""
import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

with contextlib.redirect_stdout(io.StringIO()):
    import app as appmod  # noqa: F401  (initialises the database layer)
from accounting_app import agent_proposal_routes as routes
from accounting_app import agent_proposals as P
from accounting_app import models as M
from database.config import get_connection
from database.voucher_config_db import save_voucher_config

CID = 1


def _ledger_in(group_code):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT ledger_name FROM ledgers WHERE company_id = %s AND group_code = %s "
                    "AND COALESCE(is_active, 1) = 1 ORDER BY ledger_name LIMIT 1",
                    (CID, group_code))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


class ConfiguredCompany(unittest.TestCase):
    """Expense Debit limited to Indirect Expenses (G013) for the test."""

    def setUp(self):
        self.exp_ledger = _ledger_in("G013")
        self.debtor = _ledger_in("G007")
        self.cash = _ledger_in("G005") or _ledger_in("G006")
        if not (self.exp_ledger and self.debtor and self.cash):
            self.skipTest("needs expense, debtor and cash ledgers in company 1")
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT voucher_type, side, allowed_groups, allowed_sub_groups "
                        "FROM voucher_type_configs WHERE company_id = %s", (CID,))
            self.original = cur.fetchall()
        finally:
            conn.close()
        with contextlib.redirect_stdout(io.StringIO()):
            save_voucher_config("Expense", "Debit", ["G013"], [], company_id=CID)

    def tearDown(self):
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM voucher_type_configs WHERE company_id = %s", (CID,))
            conn.commit()
        finally:
            conn.close()
        with contextlib.redirect_stdout(io.StringIO()):
            for vt, side, groups, subs in self.original:
                save_voucher_config(vt, side, json.loads(groups or "[]"),
                                    json.loads(subs or "[]"), company_id=CID)

    def expense(self, debit_ledger, vat=0):
        lines = [{"ledger_name": debit_ledger, "type": "Debit", "amount": 100}]
        if vat:
            lines.append({"ledger_name": "Input VAT 5%", "type": "Debit", "amount": vat})
        lines.append({"ledger_name": self.cash, "type": "Credit", "amount": 100 + vat})
        return {"voucher_type": "Expense", "date": "2026-09-27", "ledger_entries": lines}

    def validate(self, payload):
        with contextlib.redirect_stdout(io.StringIO()):
            return P.validate_voucher_proposal(payload, CID)


class ProposalTests(ConfiguredCompany):

    def test_an_allowed_ledger_is_accepted(self):
        self.assertEqual(self.validate(self.expense(self.exp_ledger))["voucher_type"], "Expense")

    def test_vat_the_app_books_is_not_refused(self):
        self.validate(self.expense(self.exp_ledger, vat=5))

    def test_a_ledger_the_configuration_forbids_is_refused_with_the_reason(self):
        with self.assertRaises(P.ProposalRejected) as ctx:
            self.validate(self.expense(self.debtor))
        self.assertIn("not allowed", str(ctx.exception))
        self.assertIn("Voucher Configuration", str(ctx.exception))

    def test_the_built_in_cash_rule_applies_to_proposals(self):
        receipt = {"voucher_type": "Receipt", "date": "2026-09-27", "ledger_entries": [
            {"ledger_name": self.exp_ledger, "type": "Debit", "amount": 10},
            {"ledger_name": self.debtor, "type": "Credit", "amount": 10}]}
        with self.assertRaises(P.ProposalRejected):
            self.validate(receipt)


class ApproveTests(ConfiguredCompany):
    """Approve checks again before posting - the configuration may have changed."""

    def test_approve_refuses_what_the_configuration_now_forbids(self):
        payload = self.validate(self.expense(self.exp_ledger))
        payload["ledger_entries"][0]["ledger_name"] = self.debtor   # as if filed earlier
        with mock.patch("database.add_voucher") as posted, \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError) as ctx:
                routes._post({"payload": payload}, CID)
        posted.assert_not_called()
        self.assertIn("not allowed", str(ctx.exception))

    def test_approve_posts_an_allowed_proposal(self):
        payload = self.validate(self.expense(self.exp_ledger, vat=5))
        with mock.patch("database.add_voucher", return_value="EXP-TEST") as posted, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(routes._post({"payload": payload}, CID), "EXP-TEST")
        posted.assert_called_once()


class ChatbotExpenseTests(ConfiguredCompany):
    """The chatbot's typed Expense saves through /add_voucher, which runs this."""

    def test_its_vat_line_is_not_refused(self):
        lines = self.expense(self.exp_ledger, vat=5)["ledger_entries"]
        with contextlib.redirect_stdout(io.StringIO()):
            ok, err = M.validate_voucher_ledger_groups("Expense", lines, company_id=CID)
        self.assertTrue(ok, err)

    def test_a_forbidden_expense_ledger_is_still_refused(self):
        lines = self.expense(self.debtor, vat=5)["ledger_entries"]
        with contextlib.redirect_stdout(io.StringIO()):
            ok, _err = M.validate_voucher_ledger_groups("Expense", lines, company_id=CID)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
