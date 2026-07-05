import sys
import os
import io
import json
import sqlite3
import time

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from accounting_app import create_app
from database import get_connection

def verify_import_validation():
    app = create_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    
    with app.app_context():
        conn = get_connection()
        # 1. Get User and Company
        user_row = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
        if not user_row:
            print("No user found! Cannot test login.")
            return
        user_id = user_row[0]
        
        company_row = conn.execute("SELECT id FROM companies LIMIT 1").fetchone()
        if not company_row:
            print("No company found! Cannot test.")
            return
        company_id = company_row[0]
        
        print(f"Testing with User ID: {user_id}, Company ID: {company_id}")

        # 2. Ensure Parent Group exists
        parent_group = "Fixed Assets"
        conn.execute("INSERT OR IGNORE INTO groups (company_id, group_code, group_name, nature) VALUES (?, ?, ?, ?)", 
                     (company_id, 'G999', parent_group, 'Assets'))
        conn.commit()
        
        conn.close()

        # Clear Queue for clean test
        conn = get_connection()
        conn.execute("DELETE FROM import_queue WHERE voucher_type='Sub Group'")
        conn.commit()
        conn.close()

    # Use Test Client
    with app.test_client() as client:
        # Simulate Login
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id) # Flask-Login uses string often
            sess['_fresh'] = True
            sess['company_id'] = company_id # Ensure company context
        
        # --- TEST 1: Valid Import ---
        print("\n--- TEST 1: Valid Import ---")
        param_valid = {
            "file_name": "valid_import.xlsx",
            "json_data": json.dumps([
                {"Sub Group Name": "Valid Sub Group", "Parent Group Name": parent_group}
            ])
        }
        
        resp = client.post('/queue_sub_group_import', json=param_valid)
        print(f"Response Status: {resp.status_code}")
        print(f"Response Data: {resp.get_json()}")
        
        # Verify Queue
        conn = get_connection()
        row = conn.execute("""
            SELECT validation_status, failure_reason 
            FROM import_queue 
            WHERE file_name LIKE 'SubGroup_%' AND voucher_type='Sub Group'
            ORDER BY id DESC LIMIT 1
        """).fetchone()
        conn.close()
        
        print(f"Queue Status: {row[0]}")
        if row[0] == "Success":
            print("PASS: Valid import marked as Success.")
        else:
            print(f"FAIL: Valid import marked as {row[0]}. Reason: {row[1]}")

        # --- TEST 2: Invalid Parent Group ---
        print("\n--- TEST 2: Invalid Parent Import ---")
        param_invalid = {
            "file_name": "invalid_import.xlsx",
            "json_data": json.dumps([
                {"Sub Group Name": "Invalid Sub Group", "Parent Group Name": "NonExistentParent"}
            ])
        }
        
        resp = client.post('/queue_sub_group_import', json=param_invalid)
        print(f"Response Status: {resp.status_code}")
        
        # Verify Queue
        conn = get_connection()
        row = conn.execute("""
            SELECT validation_status, failure_reason 
            FROM import_queue 
            WHERE file_name LIKE 'SubGroup_%' AND voucher_type='Sub Group'
            ORDER BY id DESC LIMIT 1
        """).fetchone()
        conn.close()
        
        print(f"Queue Status: {row[0]}")
        print(f"Failure Reason: {row[1]}")
        
        if row[0] == "Failed" and "does not exist" in (row[1] or ""):
            print("PASS: Invalid import marked as Failed with correct reason.")
        else:
            print(f"FAIL: Invalid import not marked as Failed or mismatched reason.")

if __name__ == "__main__":
    verify_import_validation()
