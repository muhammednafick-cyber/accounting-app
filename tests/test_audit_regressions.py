"""Regression checks that never connect to the application's database."""
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from flask import Flask

from database import config, recurring_db
from accounting_app import mobile_api


class ConnectionTests(unittest.TestCase):
    def test_double_close_returns_connection_only_once(self):
        raw = MagicMock()
        wrapper = config.PGConnectionWrapper(raw)
        with patch.object(config, 'release_connection') as release:
            wrapper.close()
            wrapper.close()
        release.assert_called_once_with(raw)
        self.assertIsNone(wrapper.conn)

    def test_empty_batch_executes_nothing(self):
        cursor = MagicMock()
        config.PGCursorWrapper(cursor).executemany('INSERT INTO t VALUES (?)', [])
        cursor.execute.assert_not_called()

    def test_batch_reaches_driver_with_translated_placeholders(self):
        cursor = MagicMock()
        cursor.mogrify.side_effect = lambda sql, args: sql.encode()
        config.PGCursorWrapper(cursor).executemany(
            'INSERT INTO t VALUES (?)', [(1,), (2,)])
        # execute_batch folds the rows into one statement per page.
        cursor.execute.assert_called_once()
        sent = cursor.execute.call_args[0][0].decode()
        self.assertIn('%s', sent)
        self.assertNotIn('?', sent)

    def test_fetchmany_uses_driver_default(self):
        cursor = MagicMock()
        config.PGCursorWrapper(cursor).fetchmany()
        cursor.fetchmany.assert_called_once_with()

    def test_url_preserves_encoded_password_and_tls_options(self):
        url = 'postgresql://user:p%40ss@localhost/example?sslmode=require'
        with patch.object(config, '_database_url', url):
            self.assertEqual(config._connect_kwargs()['dsn'], url)


class RecurringTests(unittest.TestCase):
    def test_non_finite_amounts_are_rejected(self):
        for amount in ('NaN', 'Infinity', '-Infinity'):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                recurring_db.validate_ledger_entries([
                    {'ledger_name': 'Expense', 'type': 'Debit', 'amount': amount},
                    {'ledger_name': 'Bank', 'type': 'Credit', 'amount': amount},
                ])

    def test_month_end_does_not_skip_next_month(self):
        for due, expected in [(date(2025, 1, 31), '2025-02-28'),
                              (date(2024, 1, 31), '2024-02-29'),
                              (date(2025, 12, 31), '2026-01-31')]:
            with self.subTest(due=due):
                conn = MagicMock()
                cursor = conn.cursor.return_value
                cursor.fetchone.return_value = {
                    'ledger_details_json': '[{"ledger_name":"A","amount":10,"type":"Debit"},'
                                           '{"ledger_name":"B","amount":10,"type":"Credit"}]',
                    'voucher_type': 'Journal', 'narration': '',
                    'frequency': 'Monthly', 'next_due_date': due,
                }
                with patch.object(recurring_db, 'get_connection', return_value=conn), \
                     patch('database.vouchers_db.add_voucher', return_value='JV-1') as add:
                    recurring_db.process_recurring_entry(1, due.isoformat(), company_id=7)
                self.assertIs(add.call_args.kwargs['db_connection'], conn)
                self.assertEqual(cursor.execute.call_args.args[1], (expected, 1, 7))

    def test_invalid_template_releases_connection_and_rolls_back(self):
        conn = MagicMock()
        conn.cursor.return_value.fetchone.return_value = {'ledger_details_json': 'invalid json'}
        with patch.object(recurring_db, 'get_connection', return_value=conn):
            with self.assertRaises(ValueError):
                recurring_db.process_recurring_entry(1, '2025-01-31', company_id=7)
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()
        conn.commit.assert_not_called()

    def test_schedule_failure_rolls_back_the_voucher(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.fetchone.return_value = {
            'ledger_details_json': '[{"ledger_name":"A","amount":10,"type":"Debit"},'
                                   '{"ledger_name":"B","amount":10,"type":"Credit"}]',
            'voucher_type': 'Journal', 'narration': None,
            'frequency': 'Monthly', 'next_due_date': date(2025, 1, 31),
        }
        cursor.execute.side_effect = [None, RuntimeError('schedule update failed')]
        with patch.object(recurring_db, 'get_connection', return_value=conn), \
             patch('database.vouchers_db.add_voucher', return_value='JV-1') as add:
            with self.assertRaisesRegex(RuntimeError, 'schedule update failed'):
                recurring_db.process_recurring_entry(1, '2025-01-31', company_id=7)
        self.assertIs(add.call_args.kwargs['db_connection'], conn)
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()
        conn.commit.assert_not_called()


class MobileLoginTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(mobile_api.mobile_bp)

    def login(self, companies, company_id):
        with patch('database.master_db.get_user_by_login_id', return_value={
                'id': 1, 'password_hash': 'unused', 'username': 'tester'}), \
             patch('database.master_db.get_user_companies', return_value=companies), \
             patch('accounting_app.rate_limit.login_attempt_allowed', return_value=(True, 0)), \
             patch.object(mobile_api, 'check_password_hash', return_value=True), \
             patch.object(mobile_api, '_issue_token') as issue:
            response = self.app.test_client().post('/api/mobile/login', json={
                'login_id': 'tester', 'password': 'secret', 'company_id': company_id})
            issue.assert_not_called()
            return response

    def test_unassigned_user_cannot_choose_arbitrary_company(self):
        self.assertEqual(self.login([], 42).status_code, 403)

    def test_user_cannot_choose_another_company(self):
        self.assertEqual(self.login([{'id': 7}], 42).status_code, 403)

    def test_invalid_company_returns_validation_error(self):
        self.assertEqual(self.login([{'id': 7}], 'invalid').status_code, 400)


class ApplicationStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import accounting_app
        old_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(accounting_app, 'ensure_db_exists'), \
             patch.object(mobile_api, 'init_token_table'), \
             patch.dict(os.environ, {'SECRET_KEY': 'audit-test-key'}):
            try:
                os.chdir(directory)
                cls.app = accounting_app.create_app()
            finally:
                os.chdir(old_cwd)

    def test_paths_are_independent_of_launch_directory(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(Path(self.app.template_folder), root / 'templates')
        self.assertEqual(Path(self.app.static_folder), root / 'static')

    def test_all_templates_parse(self):
        for name in self.app.jinja_env.list_templates():
            with self.subTest(template=name):
                source, _, _ = self.app.jinja_env.loader.get_source(self.app.jinja_env, name)
                self.app.jinja_env.parse(source)

    def test_all_navigation_destinations_resolve(self):
        from flask import url_for
        from accounting_app.navigation import DESTINATIONS
        with self.app.test_request_context():
            for label, _, endpoint, args, _ in DESTINATIONS:
                with self.subTest(destination=label):
                    url_for(endpoint, **args)


if __name__ == '__main__':
    unittest.main()
