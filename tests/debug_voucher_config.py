
import sys
import os
from flask import session 

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from accounting_app import create_app
from database.voucher_config_db import get_allowed_ledgers

app = create_app()

# Set the DB path to the one we think is active
TARGET_DB = r"d:\Accounting App with Import\web-accounting-app_with import v8\Talab_Mart.db"

with app.test_request_context():
    # Simulate session
    session['db_path'] = TARGET_DB
    
    print(f"Testing with DB: {TARGET_DB}")
    
    # Test Debit
    try:
        dr = get_allowed_ledgers("Receipt", "Debit")
        print(f"Debit Ledgers Count: {len(dr)}")
        if len(dr) > 0:
            print(f"First Debit Ledger: {dr[0]}")
    except Exception as e:
        print(f"Debit Error: {e}")

    # Test Credit
    try:
        cr = get_allowed_ledgers("Receipt", "Credit")
        print(f"Credit Ledgers Count: {len(cr)}")
        if len(cr) > 0:
            print(f"First Credit Ledger: {cr[0]}")
    except Exception as e:
        print(f"Credit Error: {e}")
