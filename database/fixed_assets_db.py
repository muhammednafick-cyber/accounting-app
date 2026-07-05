# import sqlite3 - removed
from datetime import datetime
from .config import get_connection, execute_insert_returning_id
from .company_db import get_current_company_id

# ...

def add_fixed_asset(asset_name, ledger_name, purchase_date, purchase_cost, 
                   useful_life_years, depreciation_method, depreciation_rate=None, salvage_value=0, asset_code=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("Company ID is required to add a fixed asset")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        if not asset_code:
            # Auto-generate simple code per company
            cursor.execute("SELECT COUNT(*) FROM fixed_assets WHERE company_id = %s", (company_id,))
            count = cursor.fetchone()[0] or 0
            asset_code = f"FA-{str(count + 1).zfill(4)}"
            
        asset_id = execute_insert_returning_id(
            cursor,
            """
            INSERT INTO fixed_assets (company_id, asset_name, asset_code, ledger_name, purchase_date, purchase_cost, 
                                      useful_life_years, depreciation_method, depreciation_rate, salvage_value)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (company_id, asset_name, asset_code, ledger_name, purchase_date, purchase_cost, 
              useful_life_years, depreciation_method, depreciation_rate, salvage_value)
        )
        conn.commit()
        return asset_id
    finally:
        conn.close()

def get_all_assets(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    # conn.row_factory = sqlite3.Row - Removed
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM fixed_assets WHERE company_id = %s ORDER BY asset_name", (company_id,))
    assets = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return assets

def get_asset_by_id(asset_id, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    
    conn = get_connection()
    # conn.row_factory = sqlite3.Row - Removed
    cursor = conn.cursor()
    if company_id:
        cursor.execute("SELECT * FROM fixed_assets WHERE id = %s AND company_id = %s", (asset_id, company_id))
    else:
        cursor.execute("SELECT * FROM fixed_assets WHERE id = %s", (asset_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def update_fixed_asset(asset_id, asset_name, ledger_name, purchase_date, purchase_cost,
                       useful_life_years, depreciation_method, depreciation_rate=None, salvage_value=0, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE fixed_assets
            SET asset_name=%s, ledger_name=%s, purchase_date=%s, purchase_cost=%s,
                useful_life_years=%s, depreciation_method=%s, depreciation_rate=%s, salvage_value=%s
            WHERE id=%s AND company_id=%s
        """, (asset_name, ledger_name, purchase_date, purchase_cost,
              useful_life_years, depreciation_method, depreciation_rate, salvage_value,
              asset_id, company_id))
        conn.commit()
    finally:
        conn.close()


def delete_fixed_asset(asset_id, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM depreciation_log WHERE asset_id=%s AND company_id=%s", (asset_id, company_id))
        cursor.execute("DELETE FROM fixed_assets WHERE id=%s AND company_id=%s", (asset_id, company_id))
        conn.commit()
    finally:
        conn.close()


def calculate_depreciation_preview(target_date, company_id=None):
    """
    Calculates depreciation for all active assets up to target_date.
    Returns a list of proposed entries.
    Does NOT post to DB.
    """
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    # conn.row_factory = sqlite3.Row - Removed
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM fixed_assets WHERE company_id = %s AND (status = 'Active' OR status IS NULL)", (company_id,))
    assets = cursor.fetchall()
    
    proposals = []
    
    target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    
    for asset in assets:
        # Simplified Logic for MVP:
        # Calculate depreciation for the current year or month?
        # Specification says "recurring entry for other things on user choice" 
        # and "auto posting". 
        # Logic: 
        # 1. Check last depreciation date from log
        # 2. Calculate days/months since last dep
        # 3. Apply formula
        
        last_dep_date = asset['purchase_date']
        cursor.execute("SELECT MAX(depreciation_date) FROM depreciation_log WHERE asset_id = %s", (asset['id'],))
        last_log = cursor.fetchone()[0]
        if last_log:
            last_dep_date = last_log
            
        last_dt = datetime.strptime(last_dep_date, "%Y-%m-%d")
        
        if target_dt <= last_dt:
            continue # Already depreciated
            
        # Calculate amount
        cost = float(asset['purchase_cost'])
        salvage = float(asset['salvage_value'] or 0)
        life = float(asset['useful_life_years'] or 1)
        rate = float(asset['depreciation_rate'] or 0)
        
        # Determine strict time delta in days for accuracy
        days_diff = (target_dt - last_dt).days
        if days_diff <= 0:
            continue
            
        amount = 0.0
        
        if asset['depreciation_method'] == 'SLM':
            # (Cost - Salvage) / Life_Years * (Days / 365)
            # OR if rate provided: Cost * Rate * (Days / 365)
            
            if rate > 0:
                annual_dep = cost * (rate / 100.0)
            else:
                annual_dep = (cost - salvage) / life
                
            amount = annual_dep * (days_diff / 365.0)
            
        elif asset['depreciation_method'] == 'WDV':
             # WDV needs current book value.
             # Book Value = Cost - Total Dep
             cursor.execute("SELECT SUM(amount) FROM depreciation_log WHERE asset_id = %s", (asset['id'],))
             total_dep = cursor.fetchone()[0] or 0
             current_book_value = cost - total_dep
             
             if current_book_value <= salvage:
                 continue
                 
             # WDV Formula: Book Value * Rate * (Days / 365)
             # If rate not provided, WDV is hard to calc without formula 1 - (s/c)^(1/n)
             # Assume rate is mandatory for WDV in UI validation
             if rate <= 0:
                 # Fallback or error? For now 0
                 amount = 0
             else:
                 amount = current_book_value * (rate / 100.0) * (days_diff / 365.0)
        
        amount = round(amount, 2)
        
        if amount > 0:
            proposals.append({
                'asset_id': asset['id'],
                'asset_name': asset['asset_name'],
                'amount': amount,
                'period_start': last_dep_date,
                'period_end': target_date,
                'days': days_diff,
                'method': asset['depreciation_method']
            })
            
    conn.close()
    return proposals

def post_depreciation_vouchers(proposals, posting_date, company_id=None):
    """
    Accepts a list of proposals (filtered by user unchecked items in UI)
    and creates Journal Vouchers.
    """
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return None

    from .vouchers_db import add_voucher
    
    # 1. Create a single Journal Voucher for all depreciation? Or one per asset?
    # Usually one consolidated Journal Voucher is cleaner if run monthly.
    # Let's do one Voucher.
    
    total_amount = sum(p['amount'] for p in proposals)
    if total_amount <= 0:
        return None
        
    entries_ledger = []
    
    # Debit Depreciation Expense
    entries_ledger.append({
        "ledger_name": "Depreciation Expense", 
        "amount": total_amount, 
        "type": "Debit"
    })
    
    # Credit Accumulated Depreciation for each asset (or the asset account directly?)
    # Users usually prefer Accumulated Depreciation ledger.
    # But for simplicity, we credit the Asset Account directly if 'Accumulated Dep' ledger not specified?
    # Requirement: "Fixed asset depreciation calculation and auto posting"
    # Best Practice: Credit "Accumulated Depreciation" (liability/contra-asset) or the Asset Ledger itself (reducing value).
    # Let's credit the "Accumulated Depreciation" ledger if it exists, otherwise Credit the Asset Ledger.
    
    # Actually, grouping by Asset Ledger for credit side is better
    asset_credits = {}
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Ensure Depreciation Expense ledger exists
    cursor.execute("SELECT count(*) FROM ledgers WHERE ledger_name = 'Depreciation Expense' AND company_id = %s", (company_id,))
    if cursor.fetchone()[0] == 0:
         cursor.execute("INSERT INTO ledgers (company_id, ledger_code, ledger_name, group_code, opening_balance, closing_balance) VALUES (%s, 'EXP001', 'Depreciation Expense', 'G013', 0, 0)", (company_id,)) # G013 = Indirect Expenses usually
         conn.commit()
         
    processed_asset_ids = []

    for p in proposals:
        # Fetch asset ledger info
        asset = get_asset_by_id(p['asset_id'], company_id)
        # If we have a specific Accum Dep ledger for this asset, use it.
        # Otherwise use a common one or the asset ledger.
        # For now, let's assume we Credit the Asset Ledger directly (WDV style) 
        # or a generic "Accumulated Depreciation"
        
        # Let's Credit the Asset Ledger directly for WDV, or Acc Dep for SLM?
        # To keep it simple and standard: Credit the Asset Ledger (reducing balance). 
        # Wait, if we Credit Asset Ledger, the Book Value in reports drops. This is standard WDV.
        # For SLM, we usually keep Cost and Credit Acc Dep.
        
        credit_ledger = asset['ledger_name'] # Default to reducing asset value
        
        # If user wants Acc Dep, they should have set it up. 
        # Future improvement: Add 'Accumulated Depreciation Ledger' field to Asset Master.
        
        asset_credits[credit_ledger] = asset_credits.get(credit_ledger, 0) + p['amount']
        processed_asset_ids.append((p['asset_id'], p['amount'], p['method']))

    conn.close()

    for ledger, amt in asset_credits.items():
        entries_ledger.append({
            "ledger_name": ledger,
            "amount": amt,
            "type": "Credit"
        })
        
    # Create Voucher
    voucher_no = add_voucher(
        voucher_type="Journal",
        date=posting_date,
        ledger_entries=entries_ledger,
        item_entries=[],
        narration=f"Depreciation for period ending {posting_date}",
        company_id=company_id
    )
    
    # Log the depreciation
    conn = get_connection()
    cursor = conn.cursor()
    for aid, amt, method in processed_asset_ids:
        cursor.execute("INSERT INTO depreciation_log (company_id, asset_id, voucher_number, depreciation_date, amount, method_used) VALUES (%s, %s, %s, %s, %s, %s)",
                       (company_id, aid, voucher_no, posting_date, amt, method))
    conn.commit()
    conn.close()
    
    return voucher_no

def get_total_asset_value_by_ledger(ledger_name, company_id=None):
    """
    Calculates the total Book Value (WDV) of all active assets in the given ledger.
    Book Value = Purchase Cost - Total Depreciation
    """
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return 0.0

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # 1. Get Sum of Purchase Cost for Active Assets in this Ledger
        cursor.execute("""
            SELECT SUM(purchase_cost) 
            FROM fixed_assets 
            WHERE company_id = %s AND ledger_name = %s AND (status = 'Active' OR status IS NULL)
        """, (company_id, ledger_name))
        
        total_cost = cursor.fetchone()[0] or 0.0
        
        # 2. Get Sum of Depreciation for these assets
        # We need to join with fixed_assets to filter by ledger_name
        cursor.execute("""
            SELECT SUM(dl.amount)
            FROM depreciation_log dl
            JOIN fixed_assets fa ON dl.asset_id = fa.id
            WHERE fa.company_id = %s AND fa.ledger_name = %s AND (fa.status = 'Active' OR fa.status IS NULL)
        """, (company_id, ledger_name))
        
        total_dep = cursor.fetchone()[0] or 0.0
        
        current_book_value = total_cost - total_dep
        return current_book_value
        
    except Exception as e:
        print(f"Error in get_total_asset_value_by_ledger: {e}")
        return 0.0
    finally:
        conn.close()
