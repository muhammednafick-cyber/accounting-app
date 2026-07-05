
import sys
import os

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from database.company_db import create_company_profile, get_company_settings
from database.master_db import create_company, delete_user
from database.accounts_db import get_groups
from database.config import get_connection

def test_company_creation():
    print("Testing company creation and default groups...")
    
    # 1. Create a test company in master DB
    company_name = "Test Company Defaults"
    try:
        print(f"Creating company '{company_name}'...")
        conn = get_connection()
        # Clean up if exists
        conn.execute("DELETE FROM companies WHERE name = %s", (company_name,))
        conn.commit()
        conn.close()

        company_id = create_company(company_name)
        print(f"Company created with ID: {company_id}")
        
        # 2. Create profile which should trigger defaults
        print("Creating company profile...")
        success = create_company_profile(
            company_name=company_name,
            company_id=company_id
        )
        
        if success:
            print("[OK] Company profile created successfully.")
        else:
            print("[FAIL] Failed to create company profile.")
            return False
            
        # 3. Verify default groups
        print("Verifying default groups...")
        groups = get_groups(company_id=company_id)
        if len(groups) > 0:
            print(f"[OK] Found {len(groups)} groups.")
            for g in groups:
                 print(f" - {g['group_code']}: {g['group_name']} ({g['nature']})")
        else:
            print("[FAIL] No groups found! Default groups were not created.")
            return False
            
        return True

    except Exception as e:
        print(f"[ERROR] Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    test_company_creation()
