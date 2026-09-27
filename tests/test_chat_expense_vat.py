"""VAT in the chatbot's typed Expense.

It used to post a typed Expense without VAT whatever was said - "expense 105
incl VAT for Fuel" booked 105 to Fuel. The phrase is now read, the review card
shows the VAT, and the VAT rides on the expense line for the server to book to
Input VAT 5%, as the Expense page does.
"""
import io
import json
import os
import shutil
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def _script():
    with io.open(os.path.join(ROOT, 'static', 'script.js'), encoding='utf-8') as f:
        return f.read()


RUNNER = r'''
const fs = require("fs");
const s = fs.readFileSync(process.argv[1], "utf8");
const a = s.indexOf("    function parseVatPhrase"), b = s.indexOf("    function tryParseOneLineVoucher");
global.globalChatAppendMessage = () => {};
global.vaBootstrap = { vatApplicable: true };
eval(s.slice(a, b).replace("function parseVatPhrase", "global.parseVatPhrase = function")
                  .replace("function applyVatPhrase", "global.applyVatPhrase = function"));
const out = JSON.parse(process.argv[2]).map(([text, amount]) => {
    const v = parseVatPhrase(text); const c = { amount }; applyVatPhrase(v, c);
    return { cleaned: v.cleaned, total: c.amount, vat: c.vatAmount || 0 };
});
console.log(JSON.stringify(out));
'''


@unittest.skipUnless(shutil.which('node'), 'needs node')
class VatPhraseTests(unittest.TestCase):

    def run_cases(self, cases):
        result = subprocess.run(
            ['node', '-e', RUNNER, os.path.join(ROOT, 'static', 'script.js'), json.dumps(cases)],
            capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_phrases(self):
        got = self.run_cases([
            ['expense 105 incl VAT for Fuel by cash today', 105],
            ['expense 100 + vat for Rent by bank', 100],
            ['spent 100 plus 5% vat on Stationery', 100],
            ['expense 210 for Fuel vat 10 by cash', 210],
            ['expense 300 for Fuel by cash today', 300],
        ])
        self.assertEqual([(g['total'], g['vat']) for g in got],
                         [(105, 5), (105, 5), (105, 5), (210, 10), (300, 0)])
        # The phrase is out of the text the narration and ledger come from.
        self.assertEqual(got[0]['cleaned'], 'expense 105 for Fuel by cash today')


class PostingTests(unittest.TestCase):

    def test_vat_goes_on_the_expense_line_for_the_server_to_book(self):
        s = _script()
        self.assertIn("params.append('ledger_vat_amount[]', onThis ? vat.toFixed(2) : '0');", s)
        # The expense is debited without the VAT; the total is credited.
        self.assertIn("amount: Math.round((c.amount - vatAmount) * 100) / 100, type: 'Debit'", s)

    def test_the_invoice_vat_ledger_starts_on_input_vat(self):
        self.assertIn('id="aiExpenseVatLedger" class="form-control" list="aiVatLedgerList" '
                      'value="Input VAT 5%"', _script())


SUPPLIER_RUNNER = r'''
const fs = require("fs");
const s = fs.readFileSync(process.argv[1], "utf8");
const a = s.indexOf("    function parseSupplierPhrase"), b = s.indexOf("    function resolveCreditor");
global.normalizeText = (x) => (x || "").toString().trim();
eval(s.slice(a, b).replace("function parseSupplierPhrase", "global.parseSupplierPhrase = function"));
console.log(JSON.stringify(JSON.parse(process.argv[2]).map((t) => parseSupplierPhrase(t))));
'''


@unittest.skipUnless(shutil.which('node'), 'needs node')
class SupplierPhraseTests(unittest.TestCase):
    """An Expense bought on credit: the supplier is credited, not Cash/Bank."""

    def test_phrases(self):
        cases = ['expense 500 for Repairs from ABC Garage on credit invoice 7781',
                 'expense 500 on credit from ABC Garage for Repairs today',
                 'expense 300 payable to Nafi Trading for Rent inv no INV-55',
                 'expense 300 for Fuel by cash today',
                 'expense 200 for invoice printing by cash']
        result = subprocess.run(
            ['node', '-e', SUPPLIER_RUNNER, os.path.join(ROOT, 'static', 'script.js'),
             json.dumps(cases)], capture_output=True, text=True, check=True)
        got = [(g['supplier'], g['invoiceRef'], g['cleaned']) for g in json.loads(result.stdout)]
        self.assertEqual(got, [
            ('ABC Garage', '7781', 'expense 500 for Repairs'),
            ('ABC Garage', None, 'expense 500 for Repairs today'),
            ('Nafi Trading', 'INV-55', 'expense 300 for Rent'),
            (None, None, 'expense 300 for Fuel by cash today'),
            (None, None, 'expense 200 for invoice printing by cash'),
        ])


class ExpenseCardTests(unittest.TestCase):

    def test_paid_from_takes_a_supplier_and_the_lists_follow_the_configuration(self):
        s = _script()
        self.assertIn("label: 'Paid From / Supplier'", s)
        self.assertIn("only: CASH_BANK_GROUPS.concat([CREDITOR_GROUP]), side: 'Credit'", s)
        self.assertIn("const allowed = field.side ? byType[field.side] : null;", s)

    def test_the_bootstrap_carries_the_configured_lists(self):
        import contextlib
        import sys
        sys.path.insert(0, ROOT)
        with contextlib.redirect_stdout(io.StringIO()):
            import app as appmod
            from database.config import get_connection
            conn = get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT id FROM users WHERE username = 'admin'")
                user = cur.fetchone()
            finally:
                conn.close()
        if not user:
            self.skipTest('needs the admin user')
        client = appmod.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user[0])
            sess['_fresh'] = True
            sess['company_id'] = 1
        with contextlib.redirect_stdout(io.StringIO()):
            data = client.get('/api/voucher_assistant_bootstrap').get_json()
        allowed = data['allowed_ledgers']
        self.assertEqual(set(allowed), {'Receipt', 'Payment', 'Contra', 'Expense', 'Service Income'})
        # Receipt Debit is Cash/Bank by the built-in rule, so it is a list.
        self.assertIsInstance(allowed['Receipt']['Debit'], list)


if __name__ == '__main__':
    unittest.main()
