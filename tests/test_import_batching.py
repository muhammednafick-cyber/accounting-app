"""A long import must not hold the ledger tables for its whole run.

One 100,000-row import ran as a single transaction for ninety minutes. It held
locks on the ledger tables throughout, so every other import queued behind it
and timed out, and it slowed from 56 rows a second to 10 as row versions piled
up inside the open transaction.

Committing in batches fixes both. The cost is that a failure part-way leaves
earlier batches posted, so the queue entry must keep its place and say so.
"""
import unittest
from unittest.mock import patch

from accounting_app.import_routes import queue_routes


class BatchSizeTests(unittest.TestCase):
    def test_the_batch_is_large_enough_to_be_worth_it(self):
        self.assertGreaterEqual(queue_routes.VOUCHER_COMMIT_BATCH, 100)

    def test_the_batch_is_small_enough_to_release_locks_often(self):
        # At the observed 56 rows a second this is about nine seconds of lock
        # holding, not ninety minutes.
        self.assertLessEqual(queue_routes.VOUCHER_COMMIT_BATCH, 1000)


class ResumePointTests(unittest.TestCase):
    def test_the_resume_point_is_saved_on_its_own_connection(self):
        # It must not share the import's transaction: the whole point is that
        # it survives a rollback of the batch that follows it.
        with patch.object(queue_routes, "get_db_connection") as connection:
            queue_routes._save_resume_point(12, 1, 1500)
        cursor = connection.return_value.cursor.return_value
        sql, params = cursor.execute.call_args[0]
        self.assertIn("UPDATE import_queue", sql)
        self.assertIn("processed_rows", sql)
        self.assertEqual(params, (1500, 12, 1))
        connection.return_value.commit.assert_called_once()
        connection.return_value.close.assert_called_once()

    def test_a_failed_save_never_stops_the_import(self):
        with patch.object(queue_routes, "get_db_connection",
                          side_effect=RuntimeError("pool busy")):
            queue_routes._save_resume_point(12, 1, 1500)      # must not raise

    def test_the_resume_point_is_read_back(self):
        with patch.object(queue_routes, "get_db_connection") as connection:
            connection.return_value.cursor.return_value.fetchone.return_value = [2500]
            self.assertEqual(queue_routes._resume_point(12, 1), 2500)

    def test_a_missing_entry_resumes_from_the_start(self):
        with patch.object(queue_routes, "get_db_connection") as connection:
            connection.return_value.cursor.return_value.fetchone.return_value = None
            self.assertEqual(queue_routes._resume_point(12, 1), 0)

    def test_an_unreadable_resume_point_starts_from_the_beginning(self):
        # Starting over is safe only because the alternative - skipping rows we
        # cannot prove were posted - would silently drop them.
        with patch.object(queue_routes, "get_db_connection",
                          side_effect=RuntimeError("no column")):
            self.assertEqual(queue_routes._resume_point(12, 1), 0)


class PartialFailureTests(unittest.TestCase):
    """The worst possible message is a bare error: it invites re-uploading the
    whole file and posting the first part twice.

    The row that failed and the row to resume from are rarely the same. Rows
    between the last commit and the failure are rolled back, so naming the
    resume point as the failure would send someone to inspect a row that is
    perfectly fine.
    """

    def test_a_failure_in_the_very_first_row_posts_nothing(self):
        with patch.object(queue_routes, "_save_resume_point") as save:
            message = queue_routes._partial_failure_reason(
                12, 1, 0, 100080, ValueError("bad date"))
        self.assertIn("Row 1 failed", message)
        self.assertIn("bad date", message)
        self.assertIn("Nothing was posted", message)
        save.assert_not_called()

    def test_a_failure_before_the_first_commit_posts_nothing(self):
        # Row 400 failed, batch size 500: nothing had been committed yet.
        with patch.object(queue_routes, "_save_resume_point") as save:
            message = queue_routes._partial_failure_reason(
                12, 1, 399, 100080, ValueError("no such ledger"))
        self.assertIn("Row 400 failed", message)
        self.assertIn("Nothing was posted", message)
        save.assert_called_once_with(12, 1, 0)

    def test_it_names_the_row_that_actually_failed(self):
        # Row 700 failed. 500 are committed; 501-699 were rolled back. Saying
        # "row 501 failed" would send the operator to a row that is fine.
        with patch.object(queue_routes, "_save_resume_point"):
            message = queue_routes._partial_failure_reason(
                12, 1, 699, 1200, ValueError("no such ledger"))
        self.assertIn("Row 700 of 1200 failed", message)
        self.assertNotIn("row 501 failed", message)
        self.assertIn("no such ledger", message)

    def test_it_separates_what_is_posted_from_where_to_resume(self):
        with patch.object(queue_routes, "_save_resume_point"):
            message = queue_routes._partial_failure_reason(
                12, 1, 699, 1200, ValueError("x"))
        self.assertIn("Rows 1 to 500 are posted", message)
        self.assertIn("continues from row 501", message)

    def test_it_accounts_for_the_rows_that_were_rolled_back(self):
        with patch.object(queue_routes, "_save_resume_point"):
            message = queue_routes._partial_failure_reason(
                12, 1, 699, 1200, ValueError("x"))
        self.assertIn("Rows 501 to 699 were not posted", message)

    def test_a_failure_right_after_a_commit_has_nothing_rolled_back(self):
        # Row 501 failed with exactly 500 committed: no intervening rows.
        with patch.object(queue_routes, "_save_resume_point"):
            message = queue_routes._partial_failure_reason(
                12, 1, 500, 1200, ValueError("x"))
        self.assertIn("Row 501 of 1200 failed", message)
        self.assertNotIn("were not posted", message)

    def test_the_resume_point_never_claims_more_than_was_committed(self):
        with patch.object(queue_routes, "_save_resume_point") as save:
            queue_routes._partial_failure_reason(12, 1, 1233, 100080, ValueError("x"))
        save.assert_called_once_with(12, 1, 1000)


if __name__ == "__main__":
    unittest.main()
