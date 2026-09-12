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
