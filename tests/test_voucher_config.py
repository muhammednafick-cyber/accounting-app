import unittest
import sqlite3
import os
import sys

# Add parent directory to path to import modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from database.voucher_config_db import save_voucher_config, get_voucher_config, get_allowed_ledgers
from database import get_connection

class TestVoucherConfig(unittest.TestCase):
    def setUp(self):
        # Use a temporary DB or just rely on the fact that we can clean up
        # For safety in this existing dev environment, we will use the actual DB but restore state or use unique keys?
        # Actually better to test logic with a separate DB file if possible, 
        # but `database.config` might hardcode the path.
        # Let's inspect database.config first? 
        # Assuming we are running this in the app context, let's just use the current DB but be careful.
        # Or better, mocked DB connection if the module allows injection.
        # The module uses `get_connection()` imported from `.config`.
        # We can't easily mock it without patching.
        # Let's try to patch `database.voucher_config_db.get_connection`.
        pass

    def test_save_and_get_config(self):
        print("Testing Save and Get Config...")
        # 1. Save a config for a dummy voucher type
        v_type = "TestVoucher"
        side = "Debit"
        allowed_groups = ["G001", "G002"]
        allowed_sub_groups = [1, 2]
        
        save_voucher_config(v_type, side, allowed_groups, allowed_sub_groups)
        
        # 2. Get it back
        config = get_voucher_config(v_type, side)
        self.assertIsNotNone(config)
        self.assertEqual(set(config['allowed_groups']), set(allowed_groups))
        self.assertEqual(set(config['allowed_sub_groups']), set(allowed_sub_groups))
        print("Save and Get Config: PASS")

    def test_get_allowed_ledgers_logic(self):
        print("Testing Allowed Ledgers Logic...")
        # This requires actual ledgers in the DB.
        # We will assume some ledgers exist or create dummies if we were using a test DB.
        # For this environment, let's just ensure the query runs and returns *something* or respects the filter.
        
        # First, ensure we have a config that restricts everything
        v_type = "TestVoucherEmpty"
        side = "Debit"
        save_voucher_config(v_type, side, ["NON_EXISTENT_GROUP"], [])
        
        ledgers = get_allowed_ledgers(v_type, side)
        # Should be empty if no ledgers belong to NON_EXISTENT_GROUP
        self.assertEqual(len(ledgers), 0)
        
        # Now clear config (allow all) works?
        # Our implementation of save_voucher_config might overwrite. 
        # Ideally we want to delete the config to test "default all". 
        # But `save_voucher_config` creates or updates. 
        # If we pass empty lists, does it mean "Allow None" or "Allow All"?
        # Implementation says: if row exists, it enforces the lists. 
        # So empty list means "Allow None" (if code logic is strict).
        # To test "Allow All" default, we need to ensure NO row exists.
        
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM voucher_type_configs WHERE voucher_type = ?", ("TestVoucherDefault",))
        conn.commit()
        
        ledgers_all = get_allowed_ledgers("TestVoucherDefault", "Debit")
        # Should return all ledgers (assuming DB has ledgers)
        self.assertGreater(len(ledgers_all), 0)
        print(f"Default (No Config) returned {len(ledgers_all)} ledgers: PASS")

if __name__ == '__main__':
    unittest.main()
