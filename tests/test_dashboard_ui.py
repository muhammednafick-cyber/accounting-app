"""Guard the dashboard's new read-only activity endpoint against data leakage."""
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from flask import Flask
from flask_login import LoginManager
from accounting_app import api_routes
from accounting_app.models import User


class RecentVouchersTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = 'test-only'
        self.app.config.update(TESTING=True, LOGIN_DISABLED=True)
        LoginManager(self.app)
        self.app.register_blueprint(api_routes.api_bp)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['company_id'] = 7
        self.conn = MagicMock()
        self.conn.cursor.return_value.fetchall.return_value = [
            ('JV-1', date(2026, 9, 11), 'Journal', Decimal('1250.25'))]

    def get(self, user, locations=None):
        with patch.object(api_routes, 'current_user', user), \
             patch.object(api_routes, 'get_db_connection', return_value=self.conn), \
             patch('database.company_db.get_user_locations', return_value=locations or []):
            return self.client.get('/api/dashboard/recent-vouchers')

    def test_users_without_voucher_access_are_denied(self):
        response = self.get(User(1, 'reader', '', '', 0, permissions={'reports'}))
        self.assertEqual(response.status_code, 403)
        self.conn.cursor.assert_not_called()

    def test_requires_a_selected_company(self):
        with self.client.session_transaction() as session:
            session.pop('company_id')
        self.assertEqual(self.get(User(1, 'admin', '', '', 1)).status_code, 400)
        self.conn.cursor.assert_not_called()

    def test_admin_results_are_company_scoped_and_serializable(self):
        response = self.get(User(1, 'admin', '', '', 1))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, [{'number': 'JV-1', 'date': '2026-09-11',
                                        'type': 'Journal', 'amount': 1250.25}])
        sql, params = self.conn.cursor.return_value.execute.call_args.args
        self.assertIn('WHERE company_id = %s', sql)
        self.assertIn('LIMIT 5', sql)
        self.assertEqual(params, [7])
        self.conn.close.assert_called_once()

    def test_location_restrictions_and_selection_both_apply(self):
        with self.client.session_transaction() as session:
            session['active_location'] = 'Branch B'
        user = User(1, 'clerk', '', '', 0, permissions={'vouchers'})
        self.assertEqual(self.get(user, ['Branch A']).status_code, 200)
        sql, params = self.conn.cursor.return_value.execute.call_args.args
        self.assertIn('location_name = ANY(%s)', sql)
        self.assertIn('AND location_name = %s', sql)
        self.assertEqual(params, [7, ['Branch A'], 'Branch B'])

    def test_query_failure_releases_connection(self):
        self.conn.cursor.return_value.execute.side_effect = RuntimeError('query failed')
        with self.assertRaises(RuntimeError):
            self.get(User(1, 'admin', '', '', 1))
        self.conn.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()


class SidebarMarginTests(unittest.TestCase):
    """The desktop sidebar's reserved space must not survive on a phone."""

    def _css(self):
        import io, os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "static", "ui.css"), encoding="utf-8") as f:
            return f.read()

    def test_the_content_margin_is_dropped_below_1000px(self):
        # style.css gives .content margin-left:250px to clear the sidebar. With
        # the sidebar hidden that margin pushed every page off the right edge
        # and made the document wider than the screen.
        css = self._css()
        self.assertIn("@media(max-width:1000px){.content{margin-left:0", css)

    def test_style_sheet_still_reserves_the_space_on_desktop(self):
        import io, os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "static", "style.css"), encoding="utf-8") as f:
            self.assertIn("margin-left: 250px", f.read())
