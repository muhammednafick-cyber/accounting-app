from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
import database
from database import (
    add_fixed_asset, get_all_assets, get_asset_by_id,
    calculate_depreciation_preview, post_depreciation_vouchers,
    get_total_asset_value_by_ledger, update_fixed_asset, delete_fixed_asset
)
from database.accounts_db import get_ledgers
from database.company_db import get_current_company_id

fixed_asset_bp = Blueprint('fixed_asset_bp', __name__)

@fixed_asset_bp.route('/assets')
@login_required
def assets():
    assets_list = get_all_assets()
    return render_template('fixed_assets/asset_list.html', assets=assets_list, username=current_user.username)

@fixed_asset_bp.route('/asset/add', methods=['GET', 'POST'])
@login_required
def add_asset():
    if request.method == 'POST':
        try:
            name = request.form.get('asset_name')
            ledger = request.form.get('ledger_name')
            date = request.form.get('purchase_date')
            cost = float(request.form.get('purchase_cost'))
            life = float(request.form.get('useful_life_years'))
            method = request.form.get('depreciation_method')
            rate = float(request.form.get('depreciation_rate') or 0)
            salvage = float(request.form.get('salvage_value') or 0)
            
            # Validation: Check if Total Assets exceed Ledger Balance
            # 1. Get Current Ledger Balance
            all_ledgers = get_ledgers(company_id=get_current_company_id())
            ledger_balance = 0.0
            ledger_found = False
            for l in all_ledgers:
                # l is dict: {'ledger_code': ..., 'ledger_name': ..., 'group_code': ..., 'closing_balance': ..., 'credit_days': ...}
                if l['ledger_name'] == ledger: 
                    ledger_balance = float(l['closing_balance'])
                    ledger_found = True
                    break
            
            if not ledger_found:
                 raise Exception(f"Ledger '{ledger}' not found.")

            # 2. Get Existing Assets Value (WDV)
            existing_value = get_total_asset_value_by_ledger(ledger, company_id=get_current_company_id())
            
            # 3. Compare
            new_total = existing_value + cost
            if new_total > ledger_balance:
                 diff = new_total - ledger_balance
                 raise Exception(f"Validation Error: Adding this asset would exceed the General Ledger balance for '{ledger}'. Ledger Balance: {ledger_balance:,.2f}, Existing Assets (WDV): {existing_value:,.2f}, Excess: {diff:,.2f}")

            add_fixed_asset(name, ledger, date, cost, life, method, rate, salvage)
            flash('Fixed Asset added successfully', 'success')
            return redirect(url_for('fixed_asset_bp.assets'))
        except Exception as e:
            flash(str(e), 'error')
            
    # Get Fixed Asset Ledgers (Group G003)
    company_id = get_current_company_id()
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT ledger_name FROM ledgers WHERE group_code = 'G003' AND company_id = %s", (company_id,))
    fa_ledgers = [row[0] for row in cursor.fetchall()]
    conn.close()
            
    return render_template('fixed_assets/asset_form.html', fa_ledgers=fa_ledgers, username=current_user.username)

@fixed_asset_bp.route('/asset/edit/<int:asset_id>', methods=['GET', 'POST'])
@login_required
def edit_asset(asset_id):
    company_id = get_current_company_id()
    asset = get_asset_by_id(asset_id, company_id)
    if not asset:
        flash('Asset not found.', 'error')
        return redirect(url_for('fixed_asset_bp.assets'))

    if request.method == 'POST':
        try:
            name   = request.form.get('asset_name')
            ledger = request.form.get('ledger_name')
            date   = request.form.get('purchase_date')
            cost   = float(request.form.get('purchase_cost'))
            life   = float(request.form.get('useful_life_years'))
            method = request.form.get('depreciation_method')
            rate   = float(request.form.get('depreciation_rate') or 0)
            salvage = float(request.form.get('salvage_value') or 0)
            update_fixed_asset(asset_id, name, ledger, date, cost, life, method, rate, salvage, company_id)
            flash('Asset updated successfully.', 'success')
            return redirect(url_for('fixed_asset_bp.assets'))
        except Exception as e:
            flash(str(e), 'error')

    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT ledger_name FROM ledgers WHERE group_code = 'G003' AND company_id = %s", (company_id,))
    fa_ledgers = [row[0] for row in cursor.fetchall()]
    conn.close()
    return render_template('fixed_assets/asset_form.html', fa_ledgers=fa_ledgers, asset=asset, username=current_user.username)


@fixed_asset_bp.route('/asset/delete/<int:asset_id>', methods=['POST'])
@login_required
def delete_asset(asset_id):
    try:
        delete_fixed_asset(asset_id, get_current_company_id())
        flash('Asset deleted successfully.', 'success')
    except Exception as e:
        flash(str(e), 'error')
    return redirect(url_for('fixed_asset_bp.assets'))


@fixed_asset_bp.route('/depreciation/run', methods=['GET', 'POST'])
@login_required
def run_depreciation():
    if request.method == 'POST':
        # Post Vouchers
        try:
            posting_date = request.form.get('posting_date')
            selected_assets = request.form.getlist('asset_ids[]')
            
            # Re-calculate to match what was proposed, but filter by selected
            proposals = calculate_depreciation_preview(posting_date)
            # Filter
            final_proposals = [p for p in proposals if str(p['asset_id']) in selected_assets]
            
            if not final_proposals:
                flash('No assets selected or no depreciation due.', 'warning')
                return redirect(url_for('fixed_asset_bp.run_depreciation'))
                
            voucher_no = post_depreciation_vouchers(final_proposals, posting_date)
            if voucher_no:
                flash(f'Depreciation posted successfully! Voucher: {voucher_no}', 'success')
            else:
                flash('No depreciation amount to post.', 'warning')
            return redirect(url_for('fixed_asset_bp.assets'))
            
        except Exception as e:
            flash(f"Error posting depreciation: {str(e)}", 'error')
            
    # Preview
    target_date = request.args.get('date')
    proposals = []
    if target_date:
        proposals = calculate_depreciation_preview(target_date)
        
    return render_template('fixed_assets/depreciation_run.html', 
                           proposals=proposals, 
                           target_date=target_date,
                           username=current_user.username)
