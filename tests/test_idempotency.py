"""A guarded submission posts once, however many times it arrives (R2)."""
import unittest
from unittest.mock import patch

from flask import Flask, jsonify, redirect

from accounting_app.idempotency import guard_duplicate_submission


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.calls = []

        @self.app.route("/post", methods=["POST"])
        @guard_duplicate_submission
        def post():
            self.calls.append(1)
            return jsonify(success=True, voucher_number="JV-1")

        @self.app.route("/reject", methods=["POST"])
        @guard_duplicate_submission
        def reject():
            self.calls.append(1)
            return jsonify(success=False, message="Debit and Credit do not match"), 400

        @self.app.route("/redirecting", methods=["POST"])
        @guard_duplicate_submission
        def redirecting():
            self.calls.append(1)
            return redirect("/done")

        @self.app.route("/boom", methods=["POST"])
        @guard_duplicate_submission
        def boom():
            self.calls.append(1)
            raise RuntimeError("database went away")

        self.client = self.app.test_client()

    def _post(self, path="/post", token="tok", ajax=True, **claim):
        headers = {"X-Requested-With": "XMLHttpRequest"} if ajax else {}
        with patch("database.app_state_db.submission_claim") as claim_fn, \
             patch("database.app_state_db.submission_finish") as finish, \
             patch("database.app_state_db.submission_release") as release:
            claim_fn.return_value = claim.get("state", ("won", None))
            if "side_effect" in claim:
                claim_fn.side_effect = claim["side_effect"]
            data = {"submission_token": token} if token else {}
            response = self.client.post(path, data=data, headers=headers)
            return response, finish, release

    def test_first_submission_posts_and_records_its_outcome(self):
        response, finish, release = self._post()
        self.assertEqual(self.calls, [1])
        self.assertTrue(response.get_json()["success"])
        finish.assert_called_once()
        self.assertEqual(finish.call_args[0][1], {"json": {"success": True,
                                                          "voucher_number": "JV-1"}})
        release.assert_not_called()

    def test_repeat_of_a_finished_submission_is_not_posted_again(self):
        stored = ("done", {"json": {"success": True, "voucher_number": "JV-1"}})
        response, finish, _ = self._post(state=stored)
        self.assertEqual(self.calls, [])
        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertTrue(body["duplicate"])
        self.assertEqual(body["voucher_number"], "JV-1")
        finish.assert_not_called()

    def test_repeat_while_the_first_is_still_running_is_refused(self):
        response, _, _ = self._post(state=("pending", None))
        self.assertEqual(self.calls, [])
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.get_json()["duplicate"])

    def test_a_rejected_submission_releases_its_claim_for_a_retry(self):
        response, finish, release = self._post("/reject")
        self.assertEqual(response.status_code, 400)
        finish.assert_not_called()
        release.assert_called_once_with("tok")

    def test_a_failing_submission_releases_its_claim(self):
        # The failure still surfaces; the claim must not outlive it, or the
        # correction could never be posted under the same token.
        response, finish, release = self._post("/boom")
        self.assertEqual(response.status_code, 500)
        finish.assert_not_called()
        release.assert_called_once_with("tok")

    def test_a_redirect_replays_its_confirmation(self):
        response, finish, _ = self._post("/redirecting", ajax=False)
        self.assertEqual(response.status_code, 302)
        recorded = finish.call_args[0][1]
        self.assertTrue(recorded["redirect"].endswith("/done"))

        replay, _, _ = self._post("/redirecting", ajax=False,
                                  state=("done", recorded))
        self.assertEqual(self.calls, [1])          # the handler ran only once
        self.assertEqual(replay.status_code, 302)
        self.assertTrue(replay.headers["Location"].endswith("/done"))

    def test_a_submission_without_a_token_still_posts(self):
        response, finish, _ = self._post(token=None)
        self.assertEqual(self.calls, [1])
        self.assertTrue(response.get_json()["success"])
        finish.assert_not_called()

    def test_the_guard_never_blocks_a_posting_it_cannot_check(self):
        response, _, _ = self._post(side_effect=RuntimeError("claims table gone"))
        self.assertEqual(self.calls, [1])
        self.assertTrue(response.get_json()["success"])


if __name__ == "__main__":
    unittest.main()
