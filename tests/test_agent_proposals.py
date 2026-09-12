"""An agent may suggest a voucher. Only a person may post one.

The whole design rests on that line holding, so these tests are about the line
rather than the convenience around it.
"""
import unittest
from unittest.mock import patch

from accounting_app import agent_proposals as proposals
from accounting_app.agent_proposals import ProposalRejected


def entry(ledger, side, amount):
    return {"ledger_name": ledger, "type": side, "amount": amount}


def balanced(**overrides):
    payload = {
        "voucher_type": "Payment",
        "date": "2026-03-01",
        "narration": "office rent",
        "ledger_entries": [entry("Nafi-Cash", "Credit", 100),
                           entry("Rent", "Debit", 100)],
    }
    payload.update(overrides)
    return payload


LEDGERS = [{"ledger_name": "Nafi-Cash"}, {"ledger_name": "Rent"}]


class NothingIsPostedTests(unittest.TestCase):
    """The property everything else depends on."""

    def test_proposing_never_calls_the_posting_path(self):
        with patch.object(proposals, "_ledgers", return_value=LEDGERS), \
             patch.object(proposals, "create_proposal", return_value=7), \
             patch("database.vouchers_db.add_voucher") as posted:
            proposals.propose_voucher(balanced(), company_id=1, user_id=1)
        posted.assert_not_called()

    def test_the_module_contains_no_write_statements_against_the_books(self):
        import io, os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "accounting_app", "agent_proposals.py"),
                     encoding="utf-8") as handle:
            source = handle.read().upper()
        for statement in ("INSERT INTO VOUCHERS", "INSERT INTO LEDGER_ENTRIES",
                          "UPDATE LEDGERS", "ADD_VOUCHER("):
            self.assertNotIn(statement, source)

    def test_a_filed_proposal_starts_pending(self):
        captured = {}

        def fake_create(company_id, user_id, action_type, summary, payload):
            captured.update(company_id=company_id, user_id=user_id,
                            action_type=action_type, payload=payload)
            return 11

        with patch.object(proposals, "_ledgers", return_value=LEDGERS), \
             patch.object(proposals, "create_proposal", fake_create):
            proposal_id, summary = proposals.propose_voucher(
                balanced(), company_id=3, user_id=9)
        self.assertEqual(proposal_id, 11)
        self.assertEqual(captured["company_id"], 3)
        self.assertEqual(captured["user_id"], 9)
        self.assertEqual(captured["action_type"], "voucher")
        self.assertIn("Payment", summary)


class ValidationTests(unittest.TestCase):
    """Catch it while the agent can still correct itself."""

    def _reject(self, payload):
        with patch.object(proposals, "_ledgers", return_value=LEDGERS):
            with self.assertRaises(ProposalRejected) as caught:
                proposals.validate_voucher_proposal(payload, company_id=1)
        return str(caught.exception)

    def test_an_unbalanced_entry_is_refused(self):
        message = self._reject(balanced(ledger_entries=[
            entry("Nafi-Cash", "Credit", 100), entry("Rent", "Debit", 90)]))
        self.assertIn("does not balance", message)
        self.assertIn("100", message)

    def test_an_unknown_ledger_is_refused_by_name(self):
        message = self._reject(balanced(ledger_entries=[
            entry("Nafi-Cash", "Credit", 100), entry("Nonsense", "Debit", 100)]))
        self.assertIn("Nonsense", message)
        self.assertIn("list_ledgers", message)

    def test_a_missing_voucher_type_is_refused(self):
        self.assertIn("voucher type", self._reject(balanced(voucher_type="")))

    def test_a_missing_date_is_refused(self):
        self.assertIn("date", self._reject(balanced(date="")))

    def test_no_lines_is_refused(self):
        self.assertIn("At least one", self._reject(balanced(ledger_entries=[])))

    def test_a_negative_amount_is_refused(self):
        message = self._reject(balanced(ledger_entries=[
            entry("Nafi-Cash", "Credit", -100), entry("Rent", "Debit", -100)]))
        self.assertIn("negative", message)

    def test_a_non_numeric_amount_is_refused(self):
        message = self._reject(balanced(ledger_entries=[
            entry("Nafi-Cash", "Credit", "lots"), entry("Rent", "Debit", 100)]))
        self.assertIn("must be a number", message)

    def test_a_bad_side_is_refused(self):
        message = self._reject(balanced(ledger_entries=[
            entry("Nafi-Cash", "Sideways", 100), entry("Rent", "Debit", 100)]))
        self.assertIn("Debit or Credit", message)

    def test_an_enormous_proposal_is_refused(self):
        # An agent that misreads "post the invoices" should not be able to queue
        # a thousand lines for someone to check by eye.
        lines = [entry("Nafi-Cash", "Credit", 1) for _ in range(300)]
        message = self._reject(balanced(ledger_entries=lines))
        self.assertIn("import screens", message)

    def test_a_valid_proposal_comes_back_tidied(self):
        with patch.object(proposals, "_ledgers", return_value=LEDGERS):
            checked = proposals.validate_voucher_proposal(
                balanced(ledger_entries=[entry("Nafi-Cash", "credit", "100.004"),
                                         entry("Rent", "DEBIT", 100)]),
                company_id=1)
        self.assertEqual(checked["ledger_entries"][0]["type"], "Credit")
        self.assertEqual(checked["ledger_entries"][1]["type"], "Debit")
        self.assertEqual(checked["totals"]["debit"], 100.0)

    def test_an_unreadable_ledger_list_does_not_block_a_proposal(self):
        # The posting path checks the names again; refusing here would make a
        # transient database problem look like a malformed suggestion.
        with patch.object(proposals, "_ledgers", return_value=[]):
            checked = proposals.validate_voucher_proposal(balanced(), company_id=1)
        self.assertEqual(len(checked["ledger_entries"]), 2)

    def test_the_narration_cannot_be_unbounded(self):
        with patch.object(proposals, "_ledgers", return_value=LEDGERS):
            checked = proposals.validate_voucher_proposal(
                balanced(narration="x" * 5000), company_id=1)
        self.assertLessEqual(len(checked["narration"]), 500)


class DescriptionTests(unittest.TestCase):
    def test_the_summary_says_what_would_be_posted(self):
        with patch.object(proposals, "_ledgers", return_value=LEDGERS):
            checked = proposals.validate_voucher_proposal(balanced(), company_id=1)
        summary = proposals.describe(checked)
        self.assertIn("Payment", summary)
        self.assertIn("2026-03-01", summary)
        self.assertIn("100.00", summary)
        self.assertIn("office rent", summary)


class ApprovalGateTests(unittest.TestCase):
    """Approving posts a voucher, so it needs what posting one needs."""

    def setUp(self):
        from accounting_app import create_app
        with patch("accounting_app.initialize_db"):
            self.app = create_app()
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.client = self.app.test_client()

    def _sign_in(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = "1"
            session["_fresh"] = True
            session["company_id"] = 1

    def test_the_screen_needs_a_sign_in(self):
        response = self.client.get("/settings/agent-proposals")
        self.assertEqual(response.status_code, 302)
        self.assertIn("signin", response.headers["Location"])

    def test_deciding_needs_a_sign_in(self):
        response = self.client.post("/settings/agent-proposals/1",
                                    data={"action": "approve"})
        self.assertEqual(response.status_code, 302)

    def test_a_user_without_voucher_access_cannot_decide(self):
        import accounting_app.agent_proposal_routes as routes
        self._sign_in()
        with patch.object(routes, "_may_decide", return_value=False), \
             patch.object(routes, "claim_proposal") as claimed:
            response = self.client.post("/settings/agent-proposals/1",
                                        data={"action": "approve"})
        self.assertEqual(response.status_code, 403)
        claimed.assert_not_called()

    def test_an_already_decided_proposal_cannot_be_posted_again(self):
        # Claiming is what stops a double click posting twice.
        import accounting_app.agent_proposal_routes as routes
        self._sign_in()
        with patch.object(routes, "_may_decide", return_value=True), \
             patch.object(routes, "claim_proposal", return_value=None), \
             patch.object(routes, "get_proposal",
                          return_value={"status": "approved"}), \
             patch.object(routes, "_post") as posted:
            self.client.post("/settings/agent-proposals/1",
                             data={"action": "approve"})
        posted.assert_not_called()

    def test_rejecting_posts_nothing(self):
        import accounting_app.agent_proposal_routes as routes
        self._sign_in()
        proposal = {"id": 1, "payload": balanced(), "status": "deciding"}
        with patch.object(routes, "_may_decide", return_value=True), \
             patch.object(routes, "claim_proposal", return_value=proposal), \
             patch.object(routes, "settle_proposal") as settled, \
             patch.object(routes, "_post") as posted:
            self.client.post("/settings/agent-proposals/1",
                             data={"action": "reject", "note": "not right"})
        posted.assert_not_called()
        self.assertEqual(settled.call_args[0][2], "rejected")

    def test_a_failed_posting_is_recorded_as_failed_not_left_pending(self):
        # Left pending, somebody would press Approve again and get the same
        # refusal; marked failed, the reason is visible.
        import accounting_app.agent_proposal_routes as routes
        self._sign_in()
        proposal = {"id": 1, "payload": balanced(), "status": "deciding"}
        with patch.object(routes, "_may_decide", return_value=True), \
             patch.object(routes, "claim_proposal", return_value=proposal), \
             patch.object(routes, "settle_proposal") as settled, \
             patch.object(routes, "_post",
                          side_effect=Exception("no financial year")):
            self.client.post("/settings/agent-proposals/1",
                             data={"action": "approve"})
        self.assertEqual(settled.call_args[0][2], "failed")
        self.assertIn("no financial year", settled.call_args[1]["note"])

    def test_approval_posts_through_the_ordinary_path(self):
        import accounting_app.agent_proposal_routes as routes
        self._sign_in()
        proposal = {"id": 1, "payload": balanced(), "status": "deciding"}
        with patch.object(routes, "_may_decide", return_value=True), \
             patch.object(routes, "claim_proposal", return_value=proposal), \
             patch.object(routes, "settle_proposal") as settled, \
             patch.object(routes, "_record_audit"), \
             patch("database.add_voucher", return_value="FY26-PAY-000001") as posted:
            self.client.post("/settings/agent-proposals/1",
                             data={"action": "approve"})
        posted.assert_called_once()
        self.assertEqual(settled.call_args[0][2], "approved")
        self.assertEqual(settled.call_args[1]["voucher_number"], "FY26-PAY-000001")


if __name__ == "__main__":
    unittest.main()


class StockVoucherTests(unittest.TestCase):
    """A Purchase posted without its items records the cost and receives none
    of the goods. Two such vouchers reached the books before this existed."""

    def _reject(self, voucher_type):
        with patch.object(proposals, "_ledgers", return_value=LEDGERS):
            with self.assertRaises(ProposalRejected) as caught:
                proposals.validate_voucher_proposal(
                    balanced(voucher_type=voucher_type), company_id=1)
        return str(caught.exception)

    def test_a_purchase_cannot_be_proposed(self):
        message = self._reject("Purchase")
        self.assertIn("move stock", message)
        self.assertIn("import queue", message)

    def test_a_sales_voucher_cannot_be_proposed(self):
        self.assertIn("move stock", self._reject("Sales"))

    def test_returns_and_stock_movements_cannot_be_proposed(self):
        for kind in ("Purchase Return", "Sales Return", "Stock Adjustment",
                     "Inventory Transfer", "Physical Stock"):
            self.assertIn("move stock", self._reject(kind), kind)

    def test_an_unknown_type_is_refused_rather_than_assumed_safe(self):
        # An allowlist: a voucher type added to the application later must be
        # considered here, not silently inherit permission.
        self.assertIn("move stock", self._reject("Some New Type"))

    def test_money_only_vouchers_are_still_allowed(self):
        for kind in ("Payment", "Receipt", "Contra", "Journal", "Expense",
                     "Service Income", "Service Income Return"):
            with patch.object(proposals, "_ledgers", return_value=LEDGERS):
                checked = proposals.validate_voucher_proposal(
                    balanced(voucher_type=kind), company_id=1)
            self.assertEqual(checked["voucher_type"], kind)

    def test_the_type_is_normalised(self):
        with patch.object(proposals, "_ledgers", return_value=LEDGERS):
            checked = proposals.validate_voucher_proposal(
                balanced(voucher_type="payment"), company_id=1)
        self.assertEqual(checked["voucher_type"], "Payment")


ITEMS = {"Widget", "Gadget"}
SUPPLIERS = [{"ledger_name": "Acme Supplies"}]


def purchase(**overrides):
    payload = {
        "supplier": "Acme Supplies",
        "date": "2026-09-12",
        "invoice_number": "INV-900",
        "items": [{"item_name": "Widget", "quantity": 10, "rate": 2.5}],
    }
    payload.update(overrides)
    return payload


class PurchaseProposalTests(unittest.TestCase):
    """The agent supplies what the invoice says; the accounting is built here."""

    def _check(self, payload):
        with patch.object(proposals, "_items_for", return_value=ITEMS), \
             patch.object(proposals, "_ledgers", return_value=SUPPLIERS):
            return proposals.validate_purchase_proposal(payload, company_id=1)

    def _reject(self, payload):
        with self.assertRaises(ProposalRejected) as caught:
            self._check(payload)
        return str(caught.exception)

    def test_the_double_entry_is_built_not_supplied(self):
        # The agent never chooses which ledgers move, so it cannot put the cost
        # in a purchase account instead of Inventory - which is what happened.
        out = self._check(purchase())
        self.assertEqual(out["item_entries"][0]["ledger_name"], "Inventory")
        self.assertEqual(out["item_entries"][0]["type"], "Debit")
        ledgers = {(l["ledger_name"], l["type"]) for l in out["ledger_entries"]}
        self.assertIn(("Input VAT 5%", "Debit"), ledgers)
        self.assertIn(("Acme Supplies", "Credit"), ledgers)

    def test_vat_and_totals_are_computed(self):
        out = self._check(purchase())
        self.assertEqual(out["totals"]["goods"], 25.0)
        self.assertEqual(out["totals"]["vat"], 1.25)
        self.assertEqual(out["totals"]["debit"], 26.25)

    def test_a_purchase_must_carry_items(self):
        message = self._reject(purchase(items=[]))
        self.assertIn("receives none of the goods", message)

    def test_an_unknown_item_is_refused_rather_than_created(self):
        message = self._reject(purchase(
            items=[{"item_name": "Nonesuch", "quantity": 1, "rate": 1}]))
        self.assertIn("Nonesuch", message)
        self.assertIn("must not add", message)

    def test_an_unknown_supplier_is_refused_rather_than_created(self):
        message = self._reject(purchase(supplier="Some New Vendor"))
        self.assertIn("Some New Vendor", message)
        self.assertIn("must not invent", message)

    def test_an_invoice_number_is_required(self):
        self.assertIn("invoice number", self._reject(purchase(invoice_number="")))

    def test_a_total_that_does_not_match_the_lines_is_refused(self):
        # Reading the lines wrongly is the likeliest failure; the printed total
        # is the check against it.
        message = self._reject(purchase(invoice_total=999))
        self.assertIn("does not match", message.replace("do not match", "does not match"))

    def test_a_matching_total_passes(self):
        out = self._check(purchase(invoice_total=26.25))
        self.assertEqual(out["totals"]["debit"], 26.25)

    def test_a_zero_rated_purchase_has_no_vat_line(self):
        out = self._check(purchase(vat_percent=0))
        self.assertEqual(out["totals"]["vat"], 0)
        self.assertEqual(len(out["ledger_entries"]), 1)

    def test_too_many_lines_go_to_the_import_queue(self):
        lines = [{"item_name": "Widget", "quantity": 1, "rate": 1}] * 200
        self.assertIn("import queue", self._reject(purchase(items=lines)))

    def test_the_invoice_reference_reaches_the_payload(self):
        out = self._check(purchase(invoice_date="2026-09-01"))
        self.assertEqual(out["invoice_number"], "INV-900")
        self.assertEqual(out["invoice_date"], "2026-09-01")


class PurchasePostingTests(unittest.TestCase):
    """Approval must carry the items, not an empty list."""

    def setUp(self):
        from accounting_app import create_app
        with patch("accounting_app.initialize_db"):
            self.app = create_app()
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    def test_items_and_the_invoice_reference_reach_add_voucher(self):
        import accounting_app.agent_proposal_routes as routes
        payload = {
            "voucher_type": "Purchase", "date": "2026-09-12",
            "invoice_number": "INV-900", "invoice_date": "2026-09-01",
            "narration": "n",
            "item_entries": [{"item_name": "Widget", "quantity": 10,
                              "unit_price": 2.5, "amount": 25.0,
                              "ledger_name": "Inventory", "type": "Debit"}],
            "ledger_entries": [{"ledger_name": "Acme Supplies",
                                "type": "Credit", "amount": 26.25}],
        }
        with self.app.test_request_context(), \
             patch("database.add_voucher", return_value="FY26-PUR-1") as posted:
            routes._post({"payload": payload}, company_id=1)
        args, kwargs = posted.call_args
        self.assertEqual(len(args[3]), 1, "the item lines were not passed")
        self.assertEqual(args[3][0]["item_name"], "Widget")
        self.assertEqual(kwargs["original_invoice_ref"], "INV-900")

    def test_a_purchase_with_no_items_is_refused_at_the_gate(self):
        # The last line of defence: even a malformed stored proposal cannot
        # post a purchase that receives nothing.
        import accounting_app.agent_proposal_routes as routes
        payload = {"voucher_type": "Purchase", "date": "2026-09-12",
                   "item_entries": [], "ledger_entries": []}
        with self.app.test_request_context(), \
             patch("database.add_voucher") as posted:
            with self.assertRaises(ValueError) as caught:
                routes._post({"payload": payload}, company_id=1)
        posted.assert_not_called()
        self.assertIn("receiving the goods", str(caught.exception))
