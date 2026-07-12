import psycopg2
from werkzeug.security import generate_password_hash
from database.config import get_connection

def init_unified_db():
    """
    Initialize the Unified Database Schema for PostgreSQL.
    All tables (except users, companies, user_companies) must have company_id.
    """
    conn = get_connection()
    # In PostgreSQL, foreign keys are enforced by default, no PRAGMA needed.
    cursor = conn.cursor()

    # ==========================================
    # 1. Global Tables (No company_id)
    # ==========================================
    
    # Users
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Principal flag (full access except Admin menu) — added for role management
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_principal INTEGER DEFAULT 0")
    except Exception:
        pass  # SQLite has no IF NOT EXISTS on ADD COLUMN; ignore if exists

    # Hide-dashboard flag: users land on Vouchers instead of the Dashboard
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS hide_dashboard INTEGER DEFAULT 0")
    except Exception:
        pass

    # Per-user menu permissions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_permissions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            perm_key TEXT NOT NULL,
            UNIQUE(user_id, perm_key)
        )
    """)

    # Companies
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # User-Company Access
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_company_access (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            company_id INTEGER NOT NULL,
            role TEXT DEFAULT 'User',
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE CASCADE,
            UNIQUE(user_id, company_id)
        )
    """)

    # Create Default Admin
    cursor.execute("SELECT count(*) AS cnt FROM users WHERE username = %s", ('admin',))
    result = cursor.fetchone()
    count_val = result[0] if result else 1
    if count_val == 0:
        default_password = "Admin@123"
        password_hash = generate_password_hash(default_password)
        cursor.execute(
            "INSERT INTO users (username, email, password_hash, is_admin) VALUES (%s, %s, %s, %s)",
            ('admin', 'admin@example.com', password_hash, 1)
        )
        print("Default admin created.")

    # System Settings (Global)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # ==========================================
    # 2. Company-Specific Tables (With company_id)
    # ==========================================

    # Company Settings
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS company_settings (
            company_id INTEGER PRIMARY KEY,
            company_name TEXT NOT NULL,
            vat_registration_number TEXT,
            address_line1 TEXT,
            address_line2 TEXT,
            city TEXT,
            state TEXT,
            country TEXT,
            postal_code TEXT,
            phone TEXT,
            email TEXT,
            inventory_applicable INTEGER DEFAULT 1,
            vat_applicable INTEGER DEFAULT 1,
            multiple_locations_applicable INTEGER DEFAULT 0,
            cost_center_applicable INTEGER DEFAULT 0,
            cost_center_mandatory INTEGER DEFAULT 0,
            financial_year_start TEXT,
            currency_code TEXT DEFAULT 'AED',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
        )
    """)

    # Locations (Godowns)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS locations (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            location_code TEXT NOT NULL,
            location_name TEXT NOT NULL,
            address TEXT,
            contact_person TEXT,
            phone TEXT,
            is_default INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            UNIQUE(company_id, location_code),
            UNIQUE(company_id, location_name)
        )
    """)

    # Per-user allowed locations (empty = all locations; admins always all)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_locations (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            company_id INTEGER NOT NULL,
            location_name TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            UNIQUE(user_id, company_id, location_name)
        )
    """)

    # Master Groups (NEW)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS master_groups (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            master_group_code TEXT NOT NULL,
            master_group_name TEXT NOT NULL,
            nature TEXT NOT NULL,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            UNIQUE(company_id, master_group_code),
            UNIQUE(company_id, master_group_name)
        )
    """)

    # Groups
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            group_code TEXT NOT NULL,
            group_name TEXT NOT NULL,
            nature TEXT NOT NULL,
            master_group_code TEXT,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            FOREIGN KEY (company_id, master_group_code) REFERENCES master_groups(company_id, master_group_code),
            UNIQUE(company_id, group_code),
            UNIQUE(company_id, group_name)
        )
    """)

    # Add master_group_code column to groups if it doesn't exist (for existing DBs)
    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='groups' AND column_name='master_group_code'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE groups ADD COLUMN master_group_code TEXT")
        # Add FK constraint if possible, but might fail if data exists. 
        # For now, just add column. We can enforce FK later or via app logic.
        # cursor.execute("ALTER TABLE groups ADD CONSTRAINT fk_groups_master_group FOREIGN KEY (company_id, master_group_code) REFERENCES master_groups(company_id, master_group_code)")


    # Sub Groups
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sub_groups (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            sub_group_name TEXT NOT NULL,
            group_code TEXT NOT NULL,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            FOREIGN KEY (company_id, group_code) REFERENCES groups(company_id, group_code) ON DELETE CASCADE,
            UNIQUE(company_id, sub_group_name)
        )
    """)

    # Ledgers
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ledgers (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            ledger_code TEXT NOT NULL,
            ledger_name TEXT NOT NULL,
            group_code TEXT NOT NULL,
            opening_balance DOUBLE PRECISION DEFAULT 0,
            opening_balance_type TEXT,
            closing_balance DOUBLE PRECISION DEFAULT 0,
            sub_group_id INTEGER,
            credit_days INTEGER DEFAULT 0,
            opening_balance_date TEXT,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            FOREIGN KEY (company_id, group_code) REFERENCES groups(company_id, group_code),
            FOREIGN KEY (sub_group_id) REFERENCES sub_groups(id),
            UNIQUE(company_id, ledger_code),
            UNIQUE(company_id, ledger_name)
        )
    """)

    # Add opening_balance_date column to ledgers if it doesn't exist (for existing DBs)
    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='ledgers' AND column_name='opening_balance_date'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE ledgers ADD COLUMN opening_balance_date TEXT")

    # Blocked/active flag for ledgers (blocked ledgers can't be used in new vouchers)
    cursor.execute("ALTER TABLE ledgers ADD COLUMN IF NOT EXISTS is_active INTEGER DEFAULT 1")

    # Party (Customer/Vendor) details — printed on Tax Invoices / Purchase docs
    cursor.execute("ALTER TABLE ledgers ADD COLUMN IF NOT EXISTS address TEXT")
    cursor.execute("ALTER TABLE ledgers ADD COLUMN IF NOT EXISTS contact_person TEXT")
    cursor.execute("ALTER TABLE ledgers ADD COLUMN IF NOT EXISTS phone TEXT")
    cursor.execute("ALTER TABLE ledgers ADD COLUMN IF NOT EXISTS email TEXT")
    cursor.execute("ALTER TABLE ledgers ADD COLUMN IF NOT EXISTS trn TEXT")

    # Ledger renames must cascade to tables referencing ledgers by name
    for tbl, con in (
        ('ledger_entries', 'ledger_entries_company_id_ledger_name_fkey'),
        ('item_entries', 'item_entries_company_id_ledger_name_fkey'),
        ('settlements', 'settlements_company_id_ledger_name_fkey'),
    ):
        try:
            cursor.execute(f"""
                SELECT 1 FROM pg_constraint WHERE conname = '{con}'
                  AND pg_get_constraintdef(oid) NOT ILIKE '%%ON UPDATE CASCADE%%'
            """)
            if cursor.fetchone():
                cursor.execute(f"ALTER TABLE {tbl} DROP CONSTRAINT {con}")
                cursor.execute(f"""
                    ALTER TABLE {tbl} ADD CONSTRAINT {con}
                    FOREIGN KEY (company_id, ledger_name)
                    REFERENCES ledgers(company_id, ledger_name) ON UPDATE CASCADE
                """)
        except Exception as e:
            print(f"FK cascade migration skipped for {tbl}: {e}")

    # Ledger Opening Balances per Location
    cursor.execute("SELECT to_regclass('ledger_opening_balances')")
    lob_exists = cursor.fetchone()[0] is not None
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ledger_opening_balances (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            ledger_code TEXT NOT NULL,
            location_name TEXT NOT NULL,
            opening_balance DOUBLE PRECISION DEFAULT 0,
            opening_balance_type TEXT DEFAULT 'Debit',
            opening_balance_date TEXT,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            UNIQUE(company_id, ledger_code, location_name)
        )
    """)
    if not lob_exists:
        # One-time migration: move existing single opening balances to each
        # company's default location (fallback 'Main Location').
        cursor.execute("""
            INSERT INTO ledger_opening_balances
                (company_id, ledger_code, location_name, opening_balance, opening_balance_type, opening_balance_date)
            SELECT l.company_id, l.ledger_code,
                   COALESCE((SELECT loc.location_name FROM locations loc
                             WHERE loc.company_id = l.company_id AND loc.is_default = 1 LIMIT 1), 'Main Location'),
                   l.opening_balance, COALESCE(l.opening_balance_type, 'Debit'), l.opening_balance_date
            FROM ledgers l
            WHERE COALESCE(l.opening_balance, 0) <> 0
        """)

    # Item Opening Balances per Location
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS item_opening_balances (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            item_code TEXT NOT NULL,
            location_name TEXT NOT NULL,
            quantity DOUBLE PRECISION DEFAULT 0,
            unit_price DOUBLE PRECISION DEFAULT 0,
            opening_date TEXT,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            UNIQUE(company_id, item_code, location_name)
        )
    """)

    # Cost Centers
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cost_centers (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            center_code TEXT NOT NULL,
            center_name TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            UNIQUE(company_id, center_code),
            UNIQUE(company_id, center_name)
        )
    """)

    # Inventory Groups
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory_groups (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            group_code TEXT NOT NULL,
            group_name TEXT NOT NULL,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            UNIQUE(company_id, group_code),
            UNIQUE(company_id, group_name)
        )
    """)

    # Units
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS units (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            unit_code TEXT NOT NULL,
            unit_name TEXT NOT NULL,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            UNIQUE(company_id, unit_code),
            UNIQUE(company_id, unit_name)
        )
    """)

    # Inventory Items
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            item_code TEXT NOT NULL,
            name TEXT NOT NULL,
            stock_group_code TEXT,
            unit_code TEXT,
            unit_price DOUBLE PRECISION NOT NULL,
            stock_quantity DOUBLE PRECISION DEFAULT 0,
            vat_rate DOUBLE PRECISION DEFAULT 5,
            opening_location_name TEXT,
            opening_price DOUBLE PRECISION,
            stock_value DOUBLE PRECISION DEFAULT 0,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            FOREIGN KEY (company_id, stock_group_code) REFERENCES inventory_groups(company_id, group_code),
            FOREIGN KEY (company_id, unit_code) REFERENCES units(company_id, unit_code),
            UNIQUE(company_id, item_code),
            UNIQUE(company_id, name)
        )
    """)

    # Blocked/active flag for items (blocked items can't be used in new vouchers)
    cursor.execute("ALTER TABLE inventory ADD COLUMN IF NOT EXISTS is_active INTEGER DEFAULT 1")

    # Item names must be unique per company regardless of case/spacing
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_inventory_name_ci ON inventory (company_id, LOWER(TRIM(name)))")

    # Vouchers
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vouchers (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            voucher_number TEXT NOT NULL,
            voucher_type TEXT NOT NULL,
            date TEXT NOT NULL,
            amount DOUBLE PRECISION NOT NULL,
            cost_center_code TEXT,
            narration TEXT,
            location_name TEXT,
            entry_date TEXT,
            voucher_id INTEGER, -- Legacy or internal ref
            credit_days INTEGER,
            due_date TEXT,
            original_invoice_date TEXT,
            original_invoice_ref TEXT,
            linked_voucher_number TEXT,
            posting_date TEXT,
            created_by TEXT,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            FOREIGN KEY (company_id, cost_center_code) REFERENCES cost_centers(company_id, center_code),
            UNIQUE(company_id, voucher_number)
        )
    """)

    # Add created_by column to vouchers if it doesn't exist (for existing DBs)
    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='vouchers' AND column_name='created_by'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE vouchers ADD COLUMN created_by TEXT")

    # Audit Trail: one row per action on a transaction (create / update / delete)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_trail (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            voucher_number TEXT,
            action TEXT NOT NULL,
            username TEXT,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_trail_voucher ON audit_trail (company_id, voucher_number)")

    # Additional Charge Entries
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS additional_charge_entries (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            voucher_number TEXT,
            amount DOUBLE PRECISION NOT NULL,
            valuation_method TEXT NOT NULL CHECK(valuation_method IN ('Quantity', 'Value', 'Weight (KG)')),
            narration TEXT,
            party_ledger TEXT,
            vat_amount DOUBLE PRECISION,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            FOREIGN KEY (company_id, voucher_number) REFERENCES vouchers(company_id, voucher_number) ON DELETE CASCADE
        )
    """)

    # Ledger Entries
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ledger_entries (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            voucher_number TEXT,
            ledger_name TEXT,
            amount DOUBLE PRECISION NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('Debit', 'Credit')),
            cost_center_code TEXT,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            FOREIGN KEY (company_id, voucher_number) REFERENCES vouchers(company_id, voucher_number) ON DELETE CASCADE,
            FOREIGN KEY (company_id, ledger_name) REFERENCES ledgers(company_id, ledger_name),
            FOREIGN KEY (company_id, cost_center_code) REFERENCES cost_centers(company_id, center_code)
        )
    """)

    # Item Entries
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS item_entries (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            voucher_number TEXT,
            item_name TEXT,
            quantity DOUBLE PRECISION NOT NULL,
            unit_price DOUBLE PRECISION NOT NULL,
            amount DOUBLE PRECISION NOT NULL,
            ledger_name TEXT,
            type TEXT NOT NULL CHECK(type IN ('Debit', 'Credit')),
            location_name TEXT,
            cost_center_code TEXT,
            cogs_rate DOUBLE PRECISION,
            cogs_amount DOUBLE PRECISION,
            ref_voucher_number TEXT,
            running_qty DOUBLE PRECISION,
            running_value DOUBLE PRECISION,
            running_wap DOUBLE PRECISION,
            weight_kg DOUBLE PRECISION,
            landed_cost_per_unit DOUBLE PRECISION,
            total_additional_charges_allocated DOUBLE PRECISION,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            FOREIGN KEY (company_id, voucher_number) REFERENCES vouchers(company_id, voucher_number) ON DELETE CASCADE,
            FOREIGN KEY (company_id, item_name) REFERENCES inventory(company_id, name),
            FOREIGN KEY (company_id, ledger_name) REFERENCES ledgers(company_id, ledger_name)
        )
    """)

    # Import Queue
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS import_queue (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            file_name TEXT NOT NULL,
            voucher_type TEXT NOT NULL,
            json_data TEXT NOT NULL,
            validation_status TEXT NOT NULL CHECK(validation_status IN ('Success', 'Failed')),
            upload_status TEXT NOT NULL CHECK(upload_status IN ('In Progress', 'Uploaded')),
            missing_ledger TEXT,
            failure_reason TEXT,
            missing_item TEXT,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
        )
    """)

    # Settlements
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settlements (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            settlement_number TEXT NOT NULL, 
            settlement_date TEXT NOT NULL,
            ledger_name TEXT NOT NULL,
            total_amount DOUBLE PRECISION NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            description TEXT,
            auto_posted_voucher_number TEXT,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            FOREIGN KEY (company_id, ledger_name) REFERENCES ledgers(company_id, ledger_name),
            UNIQUE(company_id, settlement_number)
        )
    """)

    # Settlement Allocations
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settlement_allocations (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            settlement_id INTEGER NOT NULL,
            ledger_entry_id INTEGER NOT NULL,
            assigned_amount DOUBLE PRECISION NOT NULL,
            type TEXT NOT NULL, 
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            FOREIGN KEY (settlement_id) REFERENCES settlements(id) ON DELETE CASCADE,
            FOREIGN KEY (ledger_entry_id) REFERENCES ledger_entries(id)
        )
    """)

    # Voucher numbering: admin-assigned next number per voucher type
    # (prefix is fixed; number can only be increased)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS voucher_number_settings (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            voucher_type TEXT NOT NULL,
            next_number INTEGER NOT NULL DEFAULT 1,
            max_number INTEGER DEFAULT 0,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            UNIQUE(company_id, voucher_type)
        )
    """)
    # Upper limit per voucher type (0 = unlimited) — for existing DBs
    cursor.execute("ALTER TABLE voucher_number_settings ADD COLUMN IF NOT EXISTS max_number INTEGER DEFAULT 0")

    # Voucher Type Configs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS voucher_type_configs (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            voucher_type TEXT NOT NULL,
            side TEXT NOT NULL CHECK(side IN ('Debit', 'Credit')),
            allowed_groups TEXT DEFAULT '[]', 
            allowed_sub_groups TEXT DEFAULT '[]',
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            UNIQUE(company_id, voucher_type, side)
        )
    """)

    # Fixed Assets
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fixed_assets (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            asset_name TEXT NOT NULL,
            asset_code TEXT,
            ledger_name TEXT NOT NULL, 
            purchase_date TEXT NOT NULL,
            purchase_cost DOUBLE PRECISION NOT NULL,
            salvage_value DOUBLE PRECISION DEFAULT 0,
            useful_life_years INTEGER,
            depreciation_method TEXT CHECK(depreciation_method IN ('SLM', 'WDV')) NOT NULL,
            depreciation_rate DOUBLE PRECISION,
            accumulated_depreciation TEXT,
            status TEXT DEFAULT 'Active',
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            UNIQUE(company_id, asset_code)
        )
    """)

    # Depreciation Log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS depreciation_log (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            asset_id INTEGER,
            voucher_number TEXT,
            depreciation_date TEXT,
            amount DOUBLE PRECISION,
            method_used TEXT,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            FOREIGN KEY (asset_id) REFERENCES fixed_assets(id)
        )
    """)

    # Financial Years
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS financial_years (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            fy_code TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            is_locked INTEGER DEFAULT 0,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            UNIQUE(company_id, fy_code)
        )
    """)

    # Recurring Templates
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recurring_templates (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            template_name TEXT NOT NULL,
            voucher_type TEXT NOT NULL,
            frequency TEXT CHECK(frequency IN ('Monthly', 'Weekly', 'Daily', 'Yearly')) NOT NULL,
            amount DOUBLE PRECISION,
            next_due_date TEXT NOT NULL,
            ledger_details_json TEXT, 
            narration TEXT,
            active INTEGER DEFAULT 1,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
        )
    """)

    # Item Mapping
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendor_item_mappings (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            vendor_name TEXT,
            vendor_item_name TEXT,
            app_item_code TEXT,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            UNIQUE(company_id, vendor_name, vendor_item_name)
        )
    """)

    # AI Settings
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ai_settings (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            setting_key TEXT NOT NULL,
            setting_value TEXT,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            UNIQUE(company_id, setting_key)
        )
    """)

    # Selling Price Master (maintained separately from the inventory item master)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS selling_prices (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            item_code TEXT NOT NULL,
            selling_price DOUBLE PRECISION NOT NULL DEFAULT 0,
            updated_at TEXT,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            UNIQUE(company_id, item_code)
        )
    """)

    # Performance indexes for the most-queried columns
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_le_company_voucher ON ledger_entries(company_id, voucher_number)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_le_company_ledger ON ledger_entries(company_id, ledger_name)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ie_company_voucher ON item_entries(company_id, voucher_number)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ie_company_item ON item_entries(company_id, item_name)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_v_company_date ON vouchers(company_id, date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_v_company_type ON vouchers(company_id, voucher_type)")

    conn.commit()
    conn.close()
    print("Unified Database (PostgreSQL) Initialized successfully.")


if __name__ == "__main__":
    init_unified_db()
