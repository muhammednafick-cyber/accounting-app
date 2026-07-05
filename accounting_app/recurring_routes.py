from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
import json
from datetime import datetime
from database import (
    add_recurring_template, get_due_recurring_entries, process_recurring_entry,
    get_ledgers
)
from database.company_db import get_current_company_id

recurring_bp = Blueprint('recurring_bp', __name__)

@recurring_bp.route('/recurring/templates')
@login_required
def templates():
    # We don't have a specific function for getting all templates yet, let's just query db directly here or add one?
    # Or reuse the due entries function? No.
    # Let's add a quick query here.
    from database import get_connection
    company_id = get_current_company_id()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM recurring_templates WHERE company_id = %s", (company_id,))
    templates = cursor.fetchall()
    conn.close()
    
    return render_template('recurring/recurring_list.html', templates=templates, username=current_user.username)

@recurring_bp.route('/recurring/add', methods=['GET', 'POST'])
@login_required
def add_template():
    if request.method == 'POST':
        try:
            name = request.form.get('template_name')
            v_type = request.form.get('voucher_type')
            freq = request.form.get('frequency')
            due_date = request.form.get('next_due_date')
            narration = request.form.get('narration')
            
            # Parse ledger entries similar to voucher route
            ledger_names = request.form.getlist("ledger_name[]")
            ledger_amounts = request.form.getlist("ledger_amount[]")
            ledger_types = request.form.getlist("ledger_type[]")
            
            ledger_entries = []
            total_amt = 0
            for lname, amt, ltype in zip(ledger_names, ledger_amounts, ledger_types):
                flt_amt = float(amt)
                ledger_entries.append({
                    "ledger_name": lname,
                    "amount": flt_amt,
                    "type": ltype,
                    "cost_center_code": None # Simplified for now
                })
                # simple total calc (sum of debits?)
                if ltype == 'Debit' and v_type != 'Receipt': # Rough logic
                    total_amt += flt_amt
                elif ltype == 'Credit' and v_type == 'Receipt':
                    total_amt += flt_amt
            
            if not total_amt and ledger_entries:
                 total_amt = sum(e['amount'] for e in ledger_entries) / 2 # Balanced voucher assumption

            add_recurring_template(
                name, v_type, freq, due_date, 
                json.dumps(ledger_entries), total_amt, narration
            )
            flash('Recurring Template added successfully', 'success')
            return redirect(url_for('recurring_bp.templates'))
        except Exception as e:
            flash(str(e), 'error')

    ledgers = get_ledgers()
    return render_template('recurring/recurring_form.html', ledgers=ledgers, username=current_user.username)

@recurring_bp.route('/recurring/process', methods=['GET', 'POST'])
@login_required
def process_entries():
    if request.method == 'POST':
        try:
            template_id = request.form.get('template_id')
            posting_date = request.form.get('posting_date') or datetime.today().strftime('%Y-%m-%d')
            
            voucher_no = process_recurring_entry(template_id, posting_date)
            
            return jsonify({'success': True, 'voucher_number': voucher_no})
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 400
            
    # GET: List due entries
    target_date = request.args.get('date') or datetime.today().strftime('%Y-%m-%d')
    due_entries = get_due_recurring_entries(target_date)
    
    return render_template('recurring/process_recurring.html', 
                           due_entries=due_entries, 
                           target_date=target_date, 
                           username=current_user.username)
