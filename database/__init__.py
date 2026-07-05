"""
Modular database package for accounting app
"""

from .config import DB_PATH, get_connection, execute_insert_returning_id
from .company_db import *
from .users_db import *
from .accounts_db import *
from .inventory_db import *
from .vouchers_db import *
from .reports_db import *
from .financial_year_db import *
from .fixed_assets_db import *
from .recurring_db import *
from .analysis_db import *
from .settlement_db import *
from .unified_db import init_unified_db
from .master_db import (
    get_all_companies as master_get_all_companies,
    get_user_companies as master_get_user_companies,
    register_company as master_register_company,
    assign_company_to_user as master_assign_company_to_user,
    remove_company_from_user as master_remove_company_from_user,
    get_company_by_id as master_get_company_by_id
)

# Initialize database function
def initialize_db():
    """Initialize all tables with unified schema"""
    
    # Initialize Unified Schema
    init_unified_db()
    
    # Post-initialization checks or seeding if needed (Global)
    # Most seeding should happen per-company (e.g., default groups/ledgers)
    # which is handled in company_db.create_company_profile or accounts_db.ensure_default_groups
    
    print("Database initialization complete via Unified DB Schema!")

__all__ = [
    'DB_PATH',
    'get_connection',
    'execute_insert_returning_id',
    'initialize_db',
    'init_unified_db',
    # Company
    'company_exists',
    'get_company_settings',
    'create_company_profile',
    'update_company_profile',
    'get_recent_companies',
    'add_recent_company',
    # Reports
    'get_coa_balances',
    # Inventory
    'ensure_default_units',
    'get_inventory_groups',
    'add_inventory_group',
    'delete_inventory_group',
    'get_units',
    'add_unit',
    'delete_unit',
    'add_inventory',
    'get_inventory_details',
    'get_items',
    'delete_inventory',
    # Locations
    'get_locations',
    'get_all_locations',
    'add_location',
    'update_location',
    'delete_location',
    'activate_location',
    'get_default_location',
    # Accounts
    'get_groups',
    'get_master_groups',
    'ensure_default_groups',
    'add_group',
    'delete_group',
    'get_ledgers',
    'get_ledger_details',
    'add_ledger',
    'delete_ledger',
    'get_cost_centers',
    'add_cost_center',
    'delete_cost_center',
    'update_cost_center_status',
    # Vouchers
    'add_voucher',
    'add_additional_charges_voucher',
    'get_voucher_details',
    'update_voucher_entries',
    'delete_voucher',
    # Master DB
    'master_get_all_companies',
    'master_get_user_companies',
    'master_register_company',
    'master_assign_company_to_user',
    'master_remove_company_from_user',
    'master_get_company_by_id',
    # Reports
    'get_ledger_transactions',
    'get_trial_balance_data',
    'get_stock_movement_data',
    'get_balance_sheet_data',
    'get_profit_and_loss_data',
    'get_closing_inventory_data',
    'get_ageing_report_data',
    'get_voucher_register_data',
    'get_sales_summary_data',
    'get_purchase_summary_data',
    'get_cash_flow_data',
    'get_negative_stock_items',
    'get_vat_summary_data',
    'get_vat_detailed_report_data',
    'get_slow_moving_items',
    # Financial Year
    'create_fy',
    'get_all_fys',
    'get_fy_by_date',
    'get_fy_by_id',
    'lock_fy',
    'reopen_fy',
    # Sub Groups
    'get_sub_groups',
    'add_sub_group',
    'delete_sub_group',
    # Fixed Assets
    'add_fixed_asset',
    'update_fixed_asset',
    'delete_fixed_asset',
    'get_all_assets',
    'get_asset_by_id',
    'calculate_depreciation_preview',
    'post_depreciation_vouchers',
    'get_total_asset_value_by_ledger',
    # Recurring
    'add_recurring_template',
    'get_due_recurring_entries',
    'process_recurring_entry',
    # Analysis
    'get_monthly_sales_trend',
    'get_monthly_purchase_trend',
    'get_top_customers',
    'get_top_suppliers',
    'get_stock_category_summary',
    'get_financial_comparison',
    'get_kpi_summary',
    # Settlement
    'create_settlement',
    'delete_settlement',
    'get_settlements_by_ledger',
    'get_settlement_details'
]
