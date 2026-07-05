
import unittest
import sys
import os
from flask import session 

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from accounting_app import create_app, login_manager
from accounting_app.models import User

# Mock user
class MockUser(User):
    def __init__(self, id, username, is_admin):
        self.id = id
        self.username = username
        self.is_admin = is_admin
    
    def get_id(self):
        return str(self.id)

class TestVoucherRoute(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()
        
        # Point to real DB
        self.db_path = r"d:\Accounting App with Import\web-accounting-app_with import v8\Talab_Mart.db"

    def test_voucher_receipt_allowed_ledgers(self):
        # Login
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['_user_id'] = '1'
            sess['db_path'] = self.db_path
        
        # Need to mock user loader
        @self.app.login_manager.user_loader
        def load_user(user_id):
            return MockUser(1, "admin", True)

        response = self.client.get('/voucher/Receipt')
        self.assertEqual(response.status_code, 200)
        
        text = response.data.decode('utf-8')
        
        # Check for allowedLedgersCr
        import re
        match = re.search(r'const allowedLedgersCr = (\[.*?\]);', text)
        if match:
            print(f"allowedLedgersCr found: {match.group(1)[:200]}...") # Print first 200 chars
            if match.group(1) == '[]':
                print("FAIL: allowedLedgersCr is empty []")
            else:
                print("SUCCESS: allowedLedgersCr has content")
        else:
            print("FAIL: allowedLedgersCr variable not found in response")

        # Check allowedLedgersDr
        match_dr = re.search(r'const allowedLedgersDr = (\[.*?\]);', text)
        if match_dr:
            print(f"allowedLedgersDr found: {match_dr.group(1)[:200]}...")
            
if __name__ == '__main__':
    unittest.main()
