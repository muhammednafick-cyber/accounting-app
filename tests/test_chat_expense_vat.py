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


if __name__ == '__main__':
    unittest.main()
