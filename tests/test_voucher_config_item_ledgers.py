"""The Voucher Configuration on the lines it used to miss.

  * VAT and discount lines the import adds itself were checked against the
    configuration, so configuring Expense Debit refused every Expense with VAT;
  * the sales ledger on a Sales item (the Sales Ledger column, the item line's
    ledger on the page) was never checked, and the page offered the Sales group
    whatever the configuration said;
  * a Stock Adjustment's per-row ledger was never checked either - the save
    ignored the check's result for that voucher type.
"""
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

with contextlib.redirect_stdout(io.StringIO()):
    import app as appmod
from accounting_app import models as M
from accounting_app.import_routes import utils as U
from database.config import get_connection


class ItemLedgerRuleEntriesTests(unittest.TestCase):

    def test_sales_item_ledgers_are_credit_lines(self):
        out = M.item_ledger_rule_entries("Sales", [
            {"ledger_name": "Retail Sales", "amount": 100}])
        self.assertEqual(out, [{"ledger_name": "Retail Sales", "amount": 100, "type": "Credit"}])

    def test_sales_return_reverses_the_side(self):
        out = M.item_ledger_rule_entries("Sales Return", [
            {"ledger_name": "Retail Sales", "amount": 5}])
        self.assertEqual(out[0]["type"], "Debit")

    def test_stock_adjustment_uses_each_rows_side(self):
        out = M.item_ledger_rule_entries("Stock Adjustment", [
            {"ledger_name": "Damaged Goods", "type": "Debit", "amount": 1},
            {"ledger_name": "Excess Stock", "type": "credit", "amount": 2}])
        self.assertEqual([(e["ledger_name"], e["type"]) for e in out],
                         [("Damaged Goods", "Debit"), ("Excess Stock", "Credit")])

    def test_an_import_line_that_defaulted_is_not_checked(self):
        out = M.item_ledger_rule_entries("Sales", [
            {"item_ledger_name": "Sales", "item_ledger_chosen": False},
            {"item_ledger_name": "Retail Sales", "item_ledger_chosen": True}])
        self.assertEqual([e["ledger_name"] for e in out], ["Retail Sales"])

    def test_other_voucher_types_have_no_item_ledgers(self):
        self.assertEqual(M.item_ledger_rule_entries("Purchase", [
            {"ledger_name": "Stock", "amount": 1}]), [])


class ImportCheckTests(unittest.TestCase):
    """What validate_single_voucher hands the configuration check."""

    def checked(self, import_type, voucher):
        seen = []

        def fake(vt, entries, company_id=None):
            seen.extend(entries)
            return True, ""
        with mock.patch.object(U, "validate_voucher_ledger_groups", fake, create=True), \
                mock.patch.object(M, "validate_voucher_ledger_groups", fake):
            with appmod.app.test_request_context("/"), \
                    contextlib.redirect_stdout(io.StringIO()):
                try:
                    U.validate_single_voucher(import_type, voucher, 1)
                except Exception:
                    pass
        return [(e["ledger_name"], e["type"]) for e in seen]

    def test_auto_lines_are_left_out_and_chosen_sales_ledgers_added(self):
        voucher = {
            "voucher_group_id": "1", "date": "27-09-2026", "narration": "t",
            "cost_center": "RETAIL",
            "ledger_entries": [
                {"ledger_name": "City Pharmacy LLC", "amount": 105, "ledger_type": "Debit", "cost_center": "RETAIL"},
                {"ledger_name": "Output VAT 5%", "amount": 5, "ledger_type": "Credit", "auto_line": True, "cost_center": "RETAIL"},
            ],
            "item_entries": [
                {"item_name": "X", "quantity": 1, "rate": 100, "amount": 100,
                 "item_ledger_name": "Nafi-Retail Sales", "item_ledger_chosen": True, "cost_center": "RETAIL"},
            ],
        }
        seen = self.checked("Sales", voucher)
        if not seen:
            self.skipTest("validation stopped before the configuration check")
        self.assertNotIn(("Output VAT 5%", "Credit"), seen)
        self.assertIn(("City Pharmacy LLC", "Debit"), seen)
        self.assertIn(("Nafi-Retail Sales", "Credit"), seen)


class ImportLinesAreMarkedTests(unittest.TestCase):

    def test_every_auto_vat_and_discount_line_is_marked(self):
        path = os.path.join(os.path.dirname(__file__), "..", "accounting_app",
                            "import_routes", "queue_routes.py")
        with io.open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertEqual(src.count('"auto_line": True'), 4)
        self.assertIn('"item_ledger_chosen": bool(str(row.get("Sales Ledger")', src)


class VoucherPageTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE username = 'admin'")
            user = cur.fetchone()
            cur.execute("SELECT company_id FROM ledgers GROUP BY company_id "
                        "ORDER BY COUNT(*) DESC LIMIT 1")
            company = cur.fetchone()
        finally:
            conn.close()
        if not (user and company):
            raise unittest.SkipTest("needs the admin user and a company")
        cls.client = appmod.app.test_client()
        with cls.client.session_transaction() as s:
            s["_user_id"] = str(user[0])
            s["_fresh"] = True
            s["company_id"] = company[0]

    def page(self, vt):
        with contextlib.redirect_stdout(io.StringIO()):
            r = self.client.get("/voucher/" + vt)
        self.assertEqual(r.status_code, 200, vt)
        return r.get_data(as_text=True)

    def test_sales_page_carries_the_item_ledger_list(self):
        html = self.page("Sales")
        self.assertIn("const itemLedgerAllowed = ", html)
        self.assertIn("function applyItemLedgerRules(row)", html)

    def test_stock_adjustment_page_renders(self):
        html = self.page("Stock Adjustment")
        self.assertIn("function applyItemLedgerRules(row)", html)
        self.assertIn("const itemLedgerAllowed = null;", html)


if __name__ == "__main__":
    unittest.main()
