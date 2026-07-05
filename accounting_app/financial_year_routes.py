from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify
from flask_login import login_required
from database.financial_year_db import create_fy, get_all_fys, lock_fy, reopen_fy
from .models import setup_access_required as admin_required

financial_year_bp = Blueprint('financial_year_bp', __name__)

@financial_year_bp.route('/master/financial-years')
@login_required
@admin_required
def manage_financial_years():
    fys = get_all_fys()
    return render_template('manage_financial_years.html', fys=fys)

@financial_year_bp.route('/master/financial-years/create', methods=['POST'])
@login_required
@admin_required
def create_financial_year():
    from .models import parse_date
    fy_code = request.form.get('fy_code')
    start_date = parse_date(request.form.get('start_date'))
    end_date = parse_date(request.form.get('end_date'))

    if not fy_code or not start_date or not end_date:
        flash('All fields are required.', 'error')
        return redirect(url_for('financial_year_bp.manage_financial_years'))
    
    try:
        create_fy(fy_code, start_date, end_date)
        flash(f'Financial Year {fy_code} created successfully.', 'success')
    except Exception as e:
        flash(str(e), 'error')
    
    return redirect(url_for('financial_year_bp.manage_financial_years'))

@financial_year_bp.route('/master/financial-years/<int:fy_id>/lock', methods=['POST'])
@login_required
@admin_required
def lock_financial_year(fy_id):
    try:
        from database.vouchers_db import create_closing_entry
        lock_fy(fy_id)
        # Create Closing Voucher
        create_closing_entry(fy_id)
        flash('Financial Year locked and Closing Entries posted successfully.', 'success')
    except Exception as e:
        flash(f"Error locking FY: {str(e)}", 'error')
    return redirect(url_for('financial_year_bp.manage_financial_years'))

@financial_year_bp.route('/master/financial-years/<int:fy_id>/reopen', methods=['POST'])
@login_required
@admin_required
def reopen_financial_year(fy_id):
    try:
        from database.vouchers_db import delete_closing_entry
        reopen_fy(fy_id)
        # Delete Closing Voucher
        delete_closing_entry(fy_id)
        flash('Financial Year re-opened and Closing Entries reversed successfully.', 'success')
    except Exception as e:
        flash(f"Error re-opening FY: {str(e)}", 'error')
    return redirect(url_for('financial_year_bp.manage_financial_years'))
@financial_year_bp.route('/master/financial-years/<int:fy_id>/update', methods=['POST'])
@login_required
@admin_required
def update_financial_year(fy_id):
    from .models import parse_date
    start_date = parse_date(request.form.get('start_date'))
    end_date = parse_date(request.form.get('end_date'))

    if not start_date or not end_date:
        flash('Start Date and End Date are required.', 'error')
        return redirect(url_for('financial_year_bp.manage_financial_years'))
    
    try:
        from database.financial_year_db import update_fy
        update_fy(fy_id, start_date, end_date)
        flash(f'Financial Year updated successfully.', 'success')
    except Exception as e:
        flash(str(e), 'error')
    
    return redirect(url_for('financial_year_bp.manage_financial_years'))
