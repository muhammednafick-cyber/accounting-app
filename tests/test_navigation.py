"""The search index has to keep pointing at screens that exist.

It is a hand-kept list, so the risk is drift: an endpoint renamed or removed
leaves a search result that 404s, and a new screen never becomes findable.
These tests catch the first case outright and make the second visible.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import app as appmod
from accounting_app.navigation import DESTINATIONS
from accounting_app.models import PERMISSION_KEYS


class TestDestinations(unittest.TestCase):

    def test_every_destination_resolves_to_a_url(self):
        broken = []
        with appmod.app.test_request_context():
            from flask import url_for
            for label, where, endpoint, args, _perm in DESTINATIONS:
                try:
                    url_for(endpoint, **args)
                except Exception as exc:
                    broken.append(f"{where} > {label} -> {endpoint} ({exc})")
        self.assertEqual(broken, [], "destinations that no longer resolve:\n"
                                     + "\n".join(broken))

    def test_no_duplicate_destinations(self):
        seen = [(e, tuple(sorted(a.items()))) for _l, _w, e, a, _p in DESTINATIONS]
        self.assertEqual(len(seen), len(set(seen)))

    def test_every_permission_key_is_real(self):
        # A key that does not exist would silently hide the entry from
        # everyone, or show it to everyone, depending on can_access.
        unknown = sorted({p for *_x, p in DESTINATIONS
                          if p and p not in PERMISSION_KEYS})
        self.assertEqual(unknown, [], f"unknown permission keys: {unknown}")

    def test_labels_are_present_and_sane(self):
        for label, where, endpoint, _a, _p in DESTINATIONS:
            self.assertTrue(label.strip(), endpoint)
            self.assertTrue(where.strip(), endpoint)
            self.assertNotIn('{{', label)
            self.assertNotIn('<', label)

    def test_the_menus_are_covered(self):
        # A rough floor: every top-level area should contribute something,
        # so a whole menu cannot fall out of the index unnoticed.
        areas = {w.split(' > ')[0] for _l, w, *_r in DESTINATIONS}
        for expected in ('Accounting Master', 'Modules', 'Inventory Master',
                         'Vouchers', 'Reports', 'Setup'):
            self.assertIn(expected, areas)


class TestSearchEndpoint(unittest.TestCase):

    def client(self):
        appmod.app.config['WTF_CSRF_ENABLED'] = False
        c = appmod.app.test_client()
        with c.session_transaction() as s:
            s['_user_id'] = '1'
            s['_fresh'] = True
            s['company_id'] = 1
        return c

    def titles(self, term):
        data = self.client().get('/api/global_search?q=' + term).get_json()
        return [i['title'] for g in data['groups'] for i in g['items']]

    def test_it_finds_screens_by_name(self):
        self.assertIn('VAT Summary', self.titles('vat'))
        self.assertIn('Trial Balance', self.titles('trial'))
        self.assertIn('Chart of Accounts', self.titles('chart'))

    def test_a_submenu_item_is_findable_without_its_parent(self):
        # The whole point: "Process Due Entries" is two hovers deep.
        self.assertIn('Process Due Entries', self.titles('due entries'))

    def test_words_can_be_given_in_any_order_and_partly(self):
        self.assertIn('Sales Register', self.titles('sales reg'))
        self.assertIn('Sales Register', self.titles('reg sales'))

    def test_a_short_or_unmatched_term_returns_nothing(self):
        for term in ('a', '', 'zzzznope'):
            data = self.client().get('/api/global_search?q=' + term).get_json()
            self.assertEqual(data['groups'], [], term)

    def test_results_carry_a_usable_url(self):
        data = self.client().get('/api/global_search?q=trial').get_json()
        for group in data['groups']:
            for item in group['items']:
                self.assertTrue(item['url'].startswith('/'), item)


if __name__ == '__main__':
    unittest.main()
