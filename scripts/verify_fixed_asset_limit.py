import os
import sys
import sqlite3
from datetime import datetime

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from database import (
    initialize_db, create_company_profile,
    add_group, add_ledger, add_voucher,
    add_fixed_asset, get_total_asset_value_by_ledger,
    get_ledgers, create_fy
)
from database.config import DB_PATH

# Use a temporary DB for testing? Or just use the main one with a test company?
# Let's use the main one with a Test Company to be realistic.
TEST_DB = "test_fa.db"

def setup_test_env():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    
    # Override DB_PATH for testing (Monkey Patching config is tricky if already imported)
    # So we will just use the main DB but a unique company.
    pass

def verify_logic():
    print("Setting up verification context...")
    
    # 1. Create Company
    # Use master_register_company to create the ID
    from database.master_db import register_company, get_all_companies
    
    # Check if exists first
    companies = get_all_companies()
    company_id = None
    for c in companies:
        if c['name'] == "FA Limit Test Co":
            company_id = c['id']
            break
            
    if not company_id:
        # Create new
        # register_company(name) -> returns ID
        company_id = register_company("FA Limit Test Co")
        # Then create profile
        create_company_profile("FA Limit Test Co", company_id=company_id, country="UAE", currency_code="AED", financial_year_start="01-01")

    print(f"Created Company ID: {company_id}")
    
    # 2. Create Financial Year
    # def create_fy(fy_code, start_date, end_date, company_id=None):
    create_fy("FY2026", datetime.now().strftime("%Y-01-01"), datetime.now().strftime("%Y-12-31"), company_id=company_id)

    # 3. Create Fixed Asset Ledger
    # Ensure Group G003 exists (it does by default)
    fa_ledger = "Furniture & Fixtures"
    try:
        add_ledger("FA001", fa_ledger, "G003", 0, "Debit", company_id=company_id)
        print(f"Created Ledger: {fa_ledger}")
    except Exception as e:
        print(f"Ledger might already exist: {e}")

    # 4. Post Opening Balance or Voucher to set Ledger Balance = 10,000
    # Let's use a Journal Voucher: Debit Furniture, Credit Capital
    add_ledger("CAP001", "Capital", "G009", 0, "Credit", company_id=company_id)
    
    add_voucher(
        "Journal", 
        datetime.now().strftime("%Y-%m-%d"),
        [
            {'ledger_name': fa_ledger, 'amount': 10000, 'type': 'Debit'},
            {'ledger_name': 'Capital', 'amount': 10000, 'type': 'Credit'}
        ],
        [],
        narration="Opening Balance for Furniture",
        company_id=company_id
    )
    print("Posted Voucher: Debit Furniture 10,000")

    # 5. Verify Ledger Balance
    ledgers = get_ledgers(company_id=company_id)
    balance = 0
    for l in ledgers:
        if l[1] == fa_ledger:
            balance = float(l[3])
            break
    print(f"Current Ledger Balance: {balance}")
    if balance != 10000:
        print("ERROR: Ledger balance mismatch!")
        return

    # 6. Add Asset 1 (Cost 6,000) -> Should Pass Logic
    print("\n--- Test Case 1: Add Asset 6,000 (Expected: Pass) ---")
    cost1 = 6000
    existing_val = get_total_asset_value_by_ledger(fa_ledger, company_id=company_id)
    print(f"Existing Asset Value: {existing_val}")
    
    if existing_val + cost1 <= balance:
        print("Validation Passed.")
        add_fixed_asset("Table", fa_ledger, datetime.now().strftime("%Y-%m-%d"), cost1, 5, "SLM", 10, 0, company_id=company_id)
        print("Asset Added.")
    else:
        print("Validation Failed (Unexpected).")

    # 7. Add Asset 2 (Cost 5,000) -> Should Fail Logic (6000 + 5000 = 11000 > 10000)
    print("\n--- Test Case 2: Add Asset 5,000 (Expected: Fail) ---")
    cost2 = 5000
    existing_val = get_total_asset_value_by_ledger(fa_ledger, company_id=company_id)
    print(f"Existing Asset Value: {existing_val}")
    
    if existing_val + cost2 <= balance:
        print("Validation Passed (Unexpected!).")
        add_fixed_asset("Chair", fa_ledger, datetime.now().strftime("%Y-%m-%d"), cost2, 5, "SLM", 10, 0, company_id=company_id)
    else:
        print(f"Validation Failed (Expected). Limit: {balance}, Current: {existing_val}, New: {cost2}, Total: {existing_val + cost2}")

    # 8. Clean up (Optional, or leave for inspection)
    print("\nVerification Complete.")

if __name__ == "__main__":
    verify_logic()
