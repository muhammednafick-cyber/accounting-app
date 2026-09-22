"""Reading a purchase invoice into a proposal.

The matching is the whole risk here. A purchase booked against the wrong
supplier or the wrong item is worse than one that never got entered, so these
lean on the cases where a near-miss must NOT be taken.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from accounting_app import chat_agent_invoice as INV

LEDGERS = [
    "Nafi-ALMARAI EMIRATES COMPANY L.L.C",
    "Nafi-ALMARAI UNBILL",
    "Nafi-GULF ARK INTERNATIONAL LLC",
    "Nafi-AAK MIDDLE EAST L.L.C",
    "Nafi-Cash",
]

ITEMS = [
    "Nafi-7DAYS CAKE - 6281183000245",
    "Nafi-7DAYS CHOCLATE 55G - 86880",
    "Nafi-12W BULB E27 - H00052",
    "0150 SWIMMING GLASS - H00034",
]


def verdict(name, candidates):
    matched, score = INV.match_name(name, candidates)
    if not matched:
        return "none"
    return "use" if score >= INV.CONFIDENT else "ask"


class MatchingTests(unittest.TestCase):

    def test_the_full_name_on_an_invoice_is_taken(self):
        self.assertEqual(verdict("ALMARAI EMIRATES COMPANY L.L.C", LEDGERS), "use")
        self.assertEqual(verdict("AAK MIDDLE EAST", LEDGERS), "use")

    def test_a_shared_first_word_is_not_a_match(self):
        # "Gulf Trading" once matched GULF ARK INTERNATIONAL outright: one word
        # in common, scored as near-certainty. That is a purchase booked to the
        # wrong supplier.
        self.assertEqual(verdict("Gulf Trading LLC", LEDGERS), "none")

    def test_two_suppliers_of_the_same_name_are_asked_about(self):
        # Almarai matches two real ledgers; choosing one is the user's job.
        self.assertNotEqual(verdict("Almarai", LEDGERS), "use")

    def test_an_unknown_supplier_matches_nothing(self):
        self.assertEqual(verdict("Totally Unknown Supplier Ltd", LEDGERS), "none")

    def test_an_item_code_on_the_master_does_not_block_the_match(self):
        # Masters carry their code - "7DAYS CAKE - 86880" - and no invoice
        # prints it.
        self.assertEqual(verdict("7DAYS CAKE", ITEMS), "use")
        self.assertEqual(verdict("12W BULB E27", ITEMS), "use")

    def test_an_item_not_stocked_matches_nothing(self):
        self.assertEqual(verdict("COCA COLA 330ML", ITEMS), "none")

    def test_a_partial_item_name_is_asked_about(self):
        self.assertEqual(verdict("SWIMMING GLASS", ITEMS), "ask")

    def test_nothing_matches_nothing(self):
        self.assertEqual(INV.match_name("", LEDGERS), (None, 0.0))
        self.assertEqual(INV.match_name("Almarai", []), (None, 0.0))


class InvoiceToProposalTests(unittest.TestCase):
    """The extractor and the proposal are both stood in for: what is under
    test is the decision in between."""

    def setUp(self):
        self.filed = []
        self._extract = INV.__dict__.get("_patched_extract")

    def run_with(self, extracted, items=None, ledgers=None, aliases=None):
        import accounting_app.ai_invoice_services as AI
        import accounting_app.agent_proposals as AP

        real = (AI.extract_invoice_data_vision, AP.propose_purchase,
                INV._item_names, INV._ledger_names, INV._vendor_aliases)

        def fake_extract(file_bytes, filename, invoice_type, company_id=None):
            return extracted

        def fake_propose(payload, company_id, user_id):
            self.filed.append(payload)
            return 42, "Purchase from " + payload["supplier"]

        AI.extract_invoice_data_vision = fake_extract
        AP.propose_purchase = fake_propose
        INV._item_names = lambda company_id: (ITEMS if items is None else items)
        INV._ledger_names = lambda company_id: (LEDGERS if ledgers is None
                                                else ledgers)
        INV._vendor_aliases = lambda company_id, vendor: (aliases or {})
        try:
            return INV.invoice_to_proposal(b"x", "invoice.pdf", 1, 1)
        finally:
            (AI.extract_invoice_data_vision, AP.propose_purchase,
             INV._item_names, INV._ledger_names, INV._vendor_aliases) = real

    def good_invoice(self, **over):
        data = {
            "vendor_name": "ALMARAI EMIRATES COMPANY L.L.C",
            "invoice_number": "INV-975287",
            "invoice_date": "2026-09-20",
            "items": [{"description": "7DAYS CAKE", "quantity": 10,
                       "unit_rate": 2.5, "amount": 25.0}],
        }
        data.update(over)
        return data

    def test_a_clean_invoice_becomes_a_proposal(self):
        filed = self.run_with(self.good_invoice())
        self.assertEqual(filed["proposal_id"], 42)
        self.assertEqual(filed["lines"], 1)
        payload = self.filed[0]
        self.assertEqual(payload["supplier"], "Nafi-ALMARAI EMIRATES COMPANY L.L.C")
        self.assertEqual(payload["invoice_number"], "INV-975287")
        self.assertEqual(payload["items"][0]["item_name"],
                         "Nafi-7DAYS CAKE - 6281183000245")
        self.assertEqual(payload["items"][0]["quantity"], 10)
        self.assertEqual(payload["items"][0]["rate"], 2.5)

    def test_an_unknown_item_stops_the_whole_invoice(self):
        # Not "propose the lines that matched": a purchase missing three of its
        # ten lines looks complete on the screen, and the stock would be wrong.
        with self.assertRaises(INV.InvoiceNotUsable) as caught:
            self.run_with(self.good_invoice(items=[
                {"description": "7DAYS CAKE", "quantity": 1, "unit_rate": 2},
                {"description": "COCA COLA 330ML", "quantity": 5, "unit_rate": 1},
            ]))
        message = str(caught.exception)
        self.assertIn("COCA COLA 330ML", message)
        self.assertIn("Vendor Item Mappings", message)
        self.assertEqual(self.filed, [])

    def test_a_vendor_mapping_beats_guessing(self):
        # The company has said what this supplier calls it; that is not a
        # similarity question any more.
        filed = self.run_with(
            self.good_invoice(items=[{"description": "CC 330 X24",
                                      "quantity": 2, "unit_rate": 9}]),
            aliases={INV._clean("CC 330 X24"): "Nafi-12W BULB E27 - H00052"})
        self.assertEqual(filed["lines"], 1)
        self.assertEqual(self.filed[0]["items"][0]["item_name"],
                         "Nafi-12W BULB E27 - H00052")

    def test_an_unknown_supplier_is_reported_not_invented(self):
        with self.assertRaises(INV.InvoiceNotUsable) as caught:
            self.run_with(self.good_invoice(vendor_name="Someone New Ltd"))
        self.assertIn("no supplier here matches that name", str(caught.exception))
        self.assertEqual(self.filed, [])

    def test_an_ambiguous_supplier_asks_rather_than_choosing(self):
        with self.assertRaises(INV.InvoiceNotUsable) as caught:
            self.run_with(self.good_invoice(vendor_name="Almarai"))
        message = str(caught.exception)
        # Two real Almarai ledgers: it must not pick, and it must say which
        # two, or the user is left guessing what to type.
        self.assertIn("ALMARAI EMIRATES", message)
        self.assertIn("ALMARAI UNBILL", message)
        self.assertEqual(self.filed, [])

    def test_an_invoice_with_no_lines_is_refused(self):
        with self.assertRaises(INV.InvoiceNotUsable) as caught:
            self.run_with(self.good_invoice(items=[]))
        self.assertIn("no line items", str(caught.exception))

    def test_a_missing_invoice_number_is_refused(self):
        with self.assertRaises(INV.InvoiceNotUsable) as caught:
            self.run_with(self.good_invoice(invoice_number=""))
        self.assertIn("invoice number", str(caught.exception))
        self.assertEqual(self.filed, [])

    def test_a_missing_date_is_refused(self):
        with self.assertRaises(INV.InvoiceNotUsable) as caught:
            self.run_with(self.good_invoice(invoice_date=""))
        self.assertIn("date", str(caught.exception))


class RouteTests(unittest.TestCase):

    def test_the_upload_route_exists_and_needs_a_sign_in(self):
        import app as appmod

        rules = {r.rule for r in appmod.app.url_map.iter_rules()}
        self.assertIn("/api/chat_agent/invoice", rules)

        # CSRF answers first for a bare client, which would hide whether the
        # sign-in check is there at all.
        appmod.app.config["WTF_CSRF_ENABLED"] = False
        try:
            response = appmod.app.test_client().post("/api/chat_agent/invoice")
        finally:
            appmod.app.config["WTF_CSRF_ENABLED"] = True
        self.assertIn(response.status_code, (302, 401, 403))

    def test_the_browser_sends_a_csrf_token_with_the_upload(self):
        # The upload is a POST like any other; it works because fetch is
        # wrapped to add the header. If that wrapper ever goes, this does too.
        import io

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "static", "script.js"),
                     encoding="utf-8") as f:
            script = f.read()
        self.assertIn("options.headers['X-CSRFToken'] = csrfToken", script)

    def test_the_attach_button_is_in_the_chat_window(self):
        import io

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "templates", "base.html"),
                     encoding="utf-8") as f:
            page = f.read()
        self.assertIn('id="vaAgentInvoiceBtn"', page)
        self.assertIn('id="vaAgentInvoiceInput"', page)

        with io.open(os.path.join(root, "static", "script.js"),
                     encoding="utf-8") as f:
            script = f.read()
        # Shown only in agent mode: the old assistant has its own upload.
        self.assertIn("function syncAgentInvoiceButton", script)
        self.assertIn("/api/chat_agent/invoice", script)


if __name__ == "__main__":
    unittest.main()
