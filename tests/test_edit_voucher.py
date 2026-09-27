"""Editing a voucher from Vouchers > Find / Edit Voucher.

  * an Expense or Service Income opened with its VAT stripped: the VAT line
    was hidden and nothing put the VAT back on the line it was charged on, so
    the voucher showed unbalanced;
  * the invoice date came back as YYYY-MM-DD into a box that only accepts
    DD-MM-YYYY, and the browser refused to submit the form;
  * line cost centres came back blank;
  * the update read the VAT tick array, which is shorter than the rows, and
    skipped the Voucher Configuration;
  * Service Income opened on a page that did not know its own type.
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
from accounting_app import voucher_routes as VR
from database.config import get_connection

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def line(name, side, amount):
    return {"ledger_name": name, "type": side, "amount": amount}


class RestoreLineVatTests(unittest.TestCase):

    def test_expense_vat_goes_back_on_the_expense_line(self):
        entries = [line("Rent", "Debit", 100), line("Supplier", "Credit", 105),
                   line("Input VAT 5%", "Debit", 5)]
        VR._restore_line_vat("Expense", entries)
        rent, supplier = entries[0], entries[1]
        self.assertTrue(rent["vat_applicable"])
        self.assertEqual((rent["vat_amount"], rent["vat_percent"]), (5, 5))
        self.assertFalse(supplier["vat_applicable"])

    def test_service_income_vat_is_on_the_credit_side(self):
        entries = [line("Service Income", "Credit", 100), line("Customer", "Debit", 105),
                   line("Output VAT 5%", "Credit", 5)]
        VR._restore_line_vat("Service Income", entries)
        self.assertEqual(entries[0]["vat_amount"], 5)
        self.assertFalse(entries[1]["vat_applicable"])

    def test_several_lines_at_five_percent_each_get_their_own(self):
        entries = [line("Rent", "Debit", 100), line("Fuel", "Debit", 33.33),
                   line("Cash", "Credit", 140), line("Input VAT 5%", "Debit", 6.67)]
        VR._restore_line_vat("Expense", entries)
        self.assertEqual([entries[0]["vat_amount"], entries[1]["vat_amount"]], [5.0, 1.67])
        self.assertAlmostEqual(entries[0]["vat_amount"] + entries[1]["vat_amount"], 6.67)

    def test_vat_on_only_some_lines_is_shared_and_still_adds_up(self):
        entries = [line("Rent", "Debit", 100), line("Fuel", "Debit", 100),
                   line("Cash", "Credit", 205), line("Input VAT 5%", "Debit", 5)]
        VR._restore_line_vat("Expense", entries)
        self.assertAlmostEqual(entries[0]["vat_amount"] + entries[1]["vat_amount"], 5)

    def test_no_vat_no_change(self):
        entries = [line("Rent", "Debit", 100), line("Cash", "Credit", 100)]
        VR._restore_line_vat("Expense", entries)
        self.assertFalse(any(e["vat_applicable"] for e in entries))

    def test_other_types_are_left_alone(self):
        entries = [line("Cash", "Debit", 100), line("Customer", "Credit", 100)]
        VR._restore_line_vat("Receipt", entries)
        self.assertNotIn("vat_applicable", entries[0])


class VoucherTypeNameTests(unittest.TestCase):

    def test_spaces_and_case_are_the_same_page(self):
        for slug in ("service_income", "service income", "Service Income", "SERVICE_INCOME"):
            self.assertEqual(VR._normalize_voucher_type(slug), "Service Income", slug)
        self.assertEqual(VR._normalize_voucher_type("receipt"), "Receipt")


class UpdateRouteTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE username = 'admin'")
            user = cur.fetchone()
        finally:
            conn.close()
        if not user:
            raise unittest.SkipTest("needs the admin user")
        appmod.app.config["WTF_CSRF_ENABLED"] = False
        cls.client = appmod.app.test_client()
        with cls.client.session_transaction() as s:
            s["_user_id"] = str(user[0])
            s["_fresh"] = True
            s["company_id"] = 1

    @classmethod
    def tearDownClass(cls):
        appmod.app.config["WTF_CSRF_ENABLED"] = True

    def post(self, form):
        with mock.patch.object(VR, "update_voucher_entries") as saved, \
                mock.patch.object(VR, "validate_voucher_ledger_groups",
                                  return_value=(True, "")) as checked, \
                contextlib.redirect_stdout(io.StringIO()):
            r = self.client.post("/update_voucher", data=form,
                                 headers={"X-Requested-With": "XMLHttpRequest"})
        return r, saved, checked

    def test_vat_is_read_from_the_amounts_not_the_tick_array(self):
        # The customer line comes first and is unticked, so its tick is not
        # submitted: the tick array is one short and misaligned.
        form = {
            "voucher_number": "FY26-EXP-TEST", "voucher_type": "Expense",
            "date": "06-09-2026", "original_invoice_date": "06-09-2026",
            "ledger_name[]": ["Supplier", "Rent"],
            "ledger_amount[]": ["105", "100"],
            "ledger_type[]": ["Credit", "Debit"],
            "ledger_vat_applicable[]": ["1"],
            "ledger_vat_amount[]": ["0", "5"],
        }
        r, saved, checked = self.post(form)
        self.assertEqual(r.status_code, 200, r.get_json())
        entries = saved.call_args[0][4]
        self.assertIn({"ledger_name": "Input VAT 5%", "amount": 5.0, "type": "Debit"}, entries)
        checked.assert_called_once()

    def test_the_configuration_is_applied(self):
        form = {
            "voucher_number": "FY26-REC-TEST", "voucher_type": "Receipt",
            "date": "06-09-2026",
            "ledger_name[]": ["Rent", "Customer"],
            "ledger_amount[]": ["10", "10"],
            "ledger_type[]": ["Debit", "Credit"],
        }
        with mock.patch.object(VR, "update_voucher_entries") as saved, \
                mock.patch.object(VR, "validate_voucher_ledger_groups",
                                  return_value=(False, "Receipt (Debit): Ledger 'Rent' is not allowed.")), \
                contextlib.redirect_stdout(io.StringIO()):
            r = self.client.post("/update_voucher", data=form,
                                 headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("not allowed", r.get_json()["message"])
        saved.assert_not_called()


class PageTests(unittest.TestCase):

    def page(self):
        with io.open(os.path.join(ROOT, "templates", "voucher.html"), encoding="utf-8") as f:
            return f.read()

    def test_invoice_date_is_shown_as_dd_mm_yyyy(self):
        self.assertIn("oidInput.value = (p.length === 3 && p[0].length === 4) ? `${p[2]}-${p[1]}-${p[0]}` : raw;",
                      self.page())

    def test_totals_and_the_balance_check_count_the_added_vat(self):
        page = self.page()
        self.assertIn("if (window.recalcBalance) window.recalcBalance();", page)
        self.assertIn("const addedVat = window.voucherInjectedVat ? window.voucherInjectedVat()", page)

    def test_service_income_has_the_edit_panel(self):
        self.assertIn("{% if voucher_type in ['Receipt', 'Payment', 'Contra', 'Journal', 'Expense',\n"
                      "                       'Service Income', 'Service Income Return'] %}", self.page())

    def test_search_sends_service_income_to_its_page(self):
        with io.open(os.path.join(ROOT, "templates", "edit_voucher_search.html"), encoding="utf-8") as f:
            search = f.read()
        self.assertIn("urlType = 'service_income'", search)
        self.assertIn("carries stock, so it cannot be edited here", search)


if __name__ == "__main__":
    unittest.main()
