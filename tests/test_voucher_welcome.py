"""The voucher-assistant welcome shows what can be typed for the chosen type.

It used to list Receipt, Payment, Contra and Expense examples whichever type was
picked. Each type now gets its own - and every example is run through the real
parser here, so the welcome can never suggest a line the chat cannot read.
"""
import io
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from accounting_app.chatbot_service import parse_voucher_message_rule_based as parse


def welcomes():
    """{voucher type: (examples, needs_ai)} as script.js defines them."""
    with io.open(os.path.join(ROOT, "static", "script.js"), encoding="utf-8") as f:
        js = f.read()
    block = js[js.index("const voucherWelcomes = {"):js.index("function voucherWelcome(vt)")]
    found = {}
    for m in re.finditer(r"(?:'([^']+)'|(\w+)):\s*\{(.*?)\n        \},", block, re.S):
        name = m.group(1) or m.group(2)
        body = m.group(3)
        examples = re.findall(r"'([^']+)'",
                              body[body.index("examples:"):body.index("]")])
        found[name] = (examples, "needsAi: true" in body)
    return found


class VoucherWelcomeTests(unittest.TestCase):

    def test_every_typed_voucher_type_has_its_own_welcome(self):
        self.assertEqual(set(welcomes()),
                         {"Receipt", "Payment", "Contra", "Expense", "Service Income"})

    def test_every_instant_example_is_read_as_its_own_type(self):
        for vt, (examples, needs_ai) in welcomes().items():
            if needs_ai:
                continue
            self.assertTrue(examples, vt)
            for example in examples:
                parsed = parse(example)
                self.assertIsNotNone(parsed, "%s: %r is not understood" % (vt, example))
                self.assertEqual(parsed["voucher_type"], vt, example)
                self.assertTrue(parsed["amount"], example)

    def test_a_type_marked_as_needing_ai_really_has_no_instant_pattern(self):
        # If the parser ever learns Service Income, the "needs AI" line becomes
        # wrong - this fails so the welcome gets updated.
        for vt, (examples, needs_ai) in welcomes().items():
            if needs_ai:
                for example in examples:
                    self.assertIsNone(parse(example), example)

    def test_no_welcome_lists_another_types_examples(self):
        for vt, (examples, _needs_ai) in welcomes().items():
            for example in examples:
                parsed = parse(example)
                if parsed:
                    self.assertEqual(parsed["voucher_type"], vt, example)

    def test_the_old_all_types_list_is_gone(self):
        with io.open(os.path.join(ROOT, "static", "script.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertNotIn("one-line examples:\\nReceipt:", js)
        self.assertEqual(js.count("voucherWelcome(assistantState.voucherType)"), 2)


if __name__ == "__main__":
    unittest.main()
