"""Large imports must not depend on the browser waiting, and must not post twice.

A 30,000-row purchase import takes about twenty minutes. Held inside the HTTP
request, nginx timed out at five and showed an error page for an import that was
still running and went on to succeed - which invites the operator to upload the
file again and post every voucher a second time.
"""
import json
import unittest
from unittest.mock import patch

from accounting_app.import_routes import queue_routes
from database import import_history_db


class RowCountTests(unittest.TestCase):
    """The size decision must never be the reason an import cannot start."""

    def test_a_list_import_counts_its_rows(self):
        with patch.object(queue_routes, "get_db_connection") as connection:
            cursor = connection.return_value.cursor.return_value
            cursor.fetchone.return_value = [json.dumps([{"a": 1}] * 42)]
            self.assertEqual(queue_routes._queued_row_count(1, 1), 42)

    def test_a_single_object_import_counts_as_one_row(self):
        with patch.object(queue_routes, "get_db_connection") as connection:
            cursor = connection.return_value.cursor.return_value
            cursor.fetchone.return_value = [json.dumps({"a": 1})]
            self.assertEqual(queue_routes._queued_row_count(1, 1), 1)

    def test_a_missing_queue_entry_counts_as_nothing(self):
        with patch.object(queue_routes, "get_db_connection") as connection:
            connection.return_value.cursor.return_value.fetchone.return_value = None
            self.assertEqual(queue_routes._queued_row_count(99, 1), 0)

    def test_a_broken_read_falls_back_to_running_inline(self):
        with patch.object(queue_routes, "get_db_connection",
                          side_effect=RuntimeError("database down")):
            self.assertEqual(queue_routes._queued_row_count(1, 1), 0)

    def test_the_connection_is_returned_even_when_the_json_is_rubbish(self):
        with patch.object(queue_routes, "get_db_connection") as connection:
            cursor = connection.return_value.cursor.return_value
            cursor.fetchone.return_value = ["{not json"]
            self.assertEqual(queue_routes._queued_row_count(1, 1), 0)
            connection.return_value.close.assert_called_once()


class FingerprintTests(unittest.TestCase):
    def test_the_same_content_fingerprints_the_same(self):
        payload = json.dumps([{"date": "2026-01-01", "amount": 10}])
        self.assertEqual(import_history_db.content_fingerprint(payload),
                         import_history_db.content_fingerprint(payload))

    def test_different_content_fingerprints_differently(self):
        a = import_history_db.content_fingerprint(json.dumps([{"amount": 10}]))
        b = import_history_db.content_fingerprint(json.dumps([{"amount": 11}]))
        self.assertNotEqual(a, b)

    def test_nothing_fingerprints_to_nothing(self):
        self.assertIsNone(import_history_db.content_fingerprint(None))


class DuplicateMessageTests(unittest.TestCase):
    def test_it_names_what_was_posted_and_when(self):
        from datetime import datetime
        message = queue_routes._duplicate_message({
            "file_name": "Purchase_2026.json",
            "row_count": 30272,
            "completed_at": datetime(2026, 9, 12, 8, 15),
        })
        self.assertIn("12-09-2026", message)
        self.assertIn("08:15", message)
        self.assertIn("30272 rows", message)
        self.assertIn("Purchase_2026.json", message)
        self.assertIn("duplicate every voucher", message)

    def test_it_still_reads_when_the_details_are_missing(self):
        message = queue_routes._duplicate_message({})
        self.assertIn("already been posted", message)
        self.assertNotIn("None", message)


class PreviouslyPostedTests(unittest.TestCase):
    def test_a_failing_history_lookup_never_blocks_an_import(self):
        with patch.object(queue_routes, "_queued_content",
                          side_effect=RuntimeError("no history table")):
            self.assertIsNone(queue_routes._previously_posted(1, 1))

    def test_a_match_is_reported(self):
        with patch.object(queue_routes, "_queued_content",
                          return_value=("[]", "abc123")), \
             patch.object(import_history_db, "find_completed_import",
                          return_value={"row_count": 5}) as lookup:
            found = queue_routes._previously_posted(7, 3)
            self.assertEqual(found, {"row_count": 5})
            lookup.assert_called_once_with(3, "abc123")


class ProgressTests(unittest.TestCase):
    def test_progress_is_silent_when_there_is_no_job(self):
        queue_routes._import_job.set(None)
        queue_routes._report_progress(10, 100)          # must not raise

    def test_progress_is_reported_against_the_job(self):
        queue_routes._import_job.set("job-1")
        try:
            with patch("accounting_app.jobs.set_progress") as set_progress:
                queue_routes._report_progress(1500, 30272)
            set_progress.assert_called_once()
            message = set_progress.call_args[0][1]
            self.assertIn("1,500", message)
            self.assertIn("30,272", message)
        finally:
            queue_routes._import_job.set(None)


class ThresholdTests(unittest.TestCase):
    def test_the_threshold_is_low_enough_to_catch_real_imports(self):
        # The file that caused this work had 30,272 rows; anything of that order
        # must never run inside a request.
        self.assertLess(queue_routes.BACKGROUND_IMPORT_ROWS, 1000)
        self.assertGreater(queue_routes.BACKGROUND_IMPORT_ROWS, 1)


if __name__ == "__main__":
    unittest.main()
