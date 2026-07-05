
import sys
import os
import unittest

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from database.company_db import create_company_profile
from database.master_db import create_company, add_user, get_user_by_username
from database.config import get_connection
from werkzeug.security import generate_password_hash

class TestGroupRoutes(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False # Disable CSRF for testing
        self.app.secret_key = 'test_secret'
        self.client = self.app.test_client()
        
        # Setup Test Data
        self.username = "test_admin_groups"
        self.password = "password"
        context = self.app.app_context()
        context.push()
        
        # Ensure user exists
        user = get_user_by_username(self.username)
        if not user:
            add_user(self.username, "test@test.com", generate_password_hash(self.password), is_admin=1)
            
        # Create test company
        self.company_name = "Route Test Company"
        conn = get_connection()
        conn.execute("DELETE FROM companies WHERE name = %s", (self.company_name,))
        conn.commit()
        conn.close()
        
        self.company_id = create_company(self.company_name)
        create_company_profile(self.company_name, company_id=self.company_id)
        
    def login(self):
        return self.client.post('/auth/signin', data=dict(
            login_id=self.username,
            password=self.password
        ), follow_redirects=True)

    def select_company(self):
        with self.client.session_transaction() as sess:
            sess['company_id'] = self.company_id
            sess['company_name'] = self.company_name
            sess['_user_id'] = get_user_by_username(self.username)['id'] # Mock login session

    def test_manage_groups(self):
        # 1. Login
        self.login()
        
        # 2. Select Company (Simulate session)
        self.select_company()
        
        # 3. Access Groups Page
        response = self.client.get('/manage-accounting-master/groups')
        
        if response.status_code == 200:
            print("[OK] Accessed Groups Page")
            content = response.data.decode('utf-8')
            # Check for default groups in HTML
            # e.g. "Sales", "Purchase"
            if "Sales" in content and "Purchase" in content:
                 print("[OK] Default groups found in response HTML")
            else:
                 print("[FAIL] Default groups NOT found in response HTML")
                 # Print snippet
                 print("Content snippet:", content[:500])
        else:
            print(f"[FAIL] Failed to access Groups Page. Status: {response.status_code}")
            if response.status_code == 302:
                print(f"Redirected to: {response.headers.get('Location')}")

if __name__ == '__main__':
    unittest.main()
