from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, send_file
from flask_login import login_required, current_user
from database.settlement_db import create_settlement, get_settlements_by_ledger, delete_settlement, get_settlement_details
from database.config import get_connection
from database.vouchers_db import add_voucher
from database.accounts_db import get_ledgers, get_cost_centers
from database.company_db import get_company_settings, get_current_company_id
from datetime import datetime
import traceback
import pandas as pd
import io

settlement_bp = Blueprint('settlement_bp', __name__)

@settlement_bp.route('/settlements')
@login_required
def index():
    # Filter ledgers for Debtors (G007) and Creditors (G008)
    ledgers = get_ledgers(group_code=['G007', 'G008'])
    # Filter ledgers for Expenses (G012, G013) and Income (G014, G015) for auto-posting difference
    expense_ledgers = get_ledgers(group_code=['G012', 'G013', 'G014', 'G015'])
    
    company = get_company_settings()
    cost_centers = get_cost_centers()
    return render_template('settlement.html', ledgers=ledgers, expense_ledgers=expense_ledgers, company=company, cost_centers=cost_centers)

@settlement_bp.route('/api/settlements/open-entries/<ledger_name>')
@login_required
def get_open_entries(ledger_name):
    company_id = get_current_company_id()
    if not company_id:
        return jsonify({'error': 'Company not selected'}), 400

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Optimized Query: Fetch entries and their total allocations in one go
        cursor.execute("""
            SELECT 
                le.id, 
                le.voucher_number, 
                le.amount, 
                le.type, 
                v.date, 
                v.narration, 
                v.voucher_type,
                COALESCE(SUM(sa.assigned_amount), 0) as allocated
            FROM ledger_entries le
            JOIN vouchers v ON le.company_id = v.company_id AND le.voucher_number = v.voucher_number
            LEFT JOIN settlement_allocations sa ON le.id = sa.ledger_entry_id
            WHERE le.company_id = %s AND le.ledger_name = %s
            GROUP BY le.id, v.date, v.voucher_number, v.narration, v.voucher_type, le.amount, le.type
            ORDER BY v.date, v.voucher_number
        """, (company_id, ledger_name))
        
        rows = cursor.fetchall()
        
        open_entries = []
        for row in rows:
            amount = float(row['amount'])
            allocated = float(row['allocated'])
            remaining = amount - allocated
            
            # Filter fully settled entries
            if remaining > 0.009:
                open_entries.append({
                    'id': row['id'],
                    'voucher_number': row['voucher_number'],
                    'date': row['date'],
                    'amount': amount,
                    'allocated': allocated,
                    'remaining': round(remaining, 2),
                    'type': row['type'],
                    'narration': row['narration'],
                    'voucher_type': row['voucher_type']
                })
                
        # Calculate Real-Time Ledger Balance
        # 1. Get Opening Balance
        cursor.execute("SELECT opening_balance, opening_balance_type FROM ledgers WHERE company_id = %s AND ledger_name = %s", (company_id, ledger_name))
        l_row = cursor.fetchone()
        
        balance = 0.0
        if l_row:
            op_bal = float(l_row['opening_balance'] or 0)
            op_type = l_row['opening_balance_type']
            
            if op_type == 'Debit':
                balance = op_bal
            else:
                balance = -op_bal
                
        # 2. Add Movements (Sum of Debits check Sum of Credits)
        cursor.execute("""
            SELECT 
                SUM(CASE WHEN type='Debit' THEN amount ELSE 0 END) as total_dr,
                SUM(CASE WHEN type='Credit' THEN amount ELSE 0 END) as total_cr
            FROM ledger_entries 
            WHERE company_id = %s AND ledger_name = %s
        """, (company_id, ledger_name))
        
        m_row = cursor.fetchone()
        if m_row:
            total_dr = float(m_row['total_dr'] or 0)
            total_cr = float(m_row['total_cr'] or 0)
            balance = balance + total_dr - total_cr

        # Result: positive = Debit, negative = Credit. Frontend handles the display logic or we return signed val.
        closing_balance = balance # Passing signed value, frontend seems to handle it (or used to handle unsigned)

        return jsonify({'entries': open_entries, 'closing_balance': closing_balance})
    except Exception as e:
        print(f"Error in get_open_entries: {str(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@settlement_bp.route('/api/settlements/match', methods=['POST'])
@login_required
def match_entries():
    company_id = get_current_company_id()
    if not company_id:
        return jsonify({'error': 'Company not selected'}), 400

    data = request.json
    ledger_name = data.get('ledger_name')
    selected_entries = data.get('entries', []) # List of {id, amount} (amount is the assigned amount)
    auto_post = data.get('auto_post', False)
    auto_post_ledger = data.get('auto_post_ledger')
    cost_center_code = data.get('cost_center')
    description = data.get('description', '')
    
    if not ledger_name or not selected_entries:
        return jsonify({'error': 'Missing ledger or entries'}), 400

    # Validation: Calculate totals
    total_dr = 0.0
    total_cr = 0.0
    
    allocations = []
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Check Cost Center Requirements
        cursor.execute("SELECT cost_center_applicable FROM company_settings WHERE company_id = %s", (company_id,))
        row = cursor.fetchone()
        if row:
            cc_applicable = row['cost_center_applicable'] if isinstance(row, dict) or hasattr(row, 'keys') else row[0]
        else:
            cc_applicable = 0
        
        if auto_post and cc_applicable and not cost_center_code:
            return jsonify({'error': 'Cost Center is required for auto-posting when Cost Center feature is enabled.'}), 400

        for entry in selected_entries:
            eid = entry['id']
            assigned = float(entry['assigned'])
            etype = entry['type'] # 'Debit' or 'Credit'
            
            allocations.append({
                'ledger_entry_id': eid,
                'assigned_amount': assigned,
                'type': etype
            })
            
            if etype == 'Debit':
                total_dr += assigned
            else:
                total_cr += assigned
                
        diff = round(total_dr - total_cr, 2)
        auto_voucher_number = None
        
        if abs(diff) > 0.009:
            if not auto_post:
                return jsonify({'error': f'Amounts do not match. Difference: {diff}'}), 400
            
            if not auto_post_ledger:
                return jsonify({'error': 'Auto-post enabled but no adjustment ledger selected'}), 400
            
            # Create Adjustment Voucher
            adj_amount = abs(diff)
            ledger_entries = []
            
            # Entry for the Party (Customer/Vendor)
            party_entry = {
                'ledger_name': ledger_name,
                'amount': adj_amount,
                'type': 'Credit' if diff > 0 else 'Debit'
            }
            ledger_entries.append(party_entry)
            
            # Entry for the Adjustment Ledger (Discount/Expense/Income)
            adj_entry = {
                'ledger_name': auto_post_ledger,
                'amount': adj_amount,
                'type': 'Debit' if diff > 0 else 'Credit',
                'cost_center_code': cost_center_code if cost_center_code else None
            }
            ledger_entries.append(adj_entry)
            
            narration =f"Settlement Adjustment: {description}"
            today_str = description.split(' - ')[0] if ' - ' in description else '' 
            # Or just use today
            # Or just use today
            date_str = datetime.today().strftime('%Y-%m-%d')
            
            # Create Voucher
            auto_voucher_number = add_voucher(
                voucher_type="Journal",
                date=date_str,
                ledger_entries=ledger_entries,
                item_entries=[],
                narration=narration,
                cost_center_code=cost_center_code, # Valid to pass to header too if supported
                db_connection=conn,  # Pass connection to be atomic
                company_id=company_id
            )
            
            # Now we need to get the ID of the newly created ledger entry for the Party
            # to add it to allocations.
            cursor.execute("""
                SELECT id FROM ledger_entries 
                WHERE company_id = %s AND voucher_number = %s AND ledger_name = %s
            """, (company_id, auto_voucher_number, ledger_name))
            
            row = cursor.fetchone()
            if row:
                new_entry_id = row['id'] if isinstance(row, dict) or hasattr(row, 'keys') else row[0]
            else:
                 raise Exception("Could not find auto-generated ledger entry for settlement.")
            
            allocations.append({
                'ledger_entry_id': new_entry_id,
                'assigned_amount': adj_amount,
                'type': party_entry['type']
            })
            
        # Create Settlement
        settlement_date = datetime.today().strftime('%Y-%m-%d')
        s_num = create_settlement(ledger_name, settlement_date, allocations, auto_voucher_number, description, company_id=company_id, db_connection=conn)
        
        conn.commit()
        return jsonify({'success': True, 'settlement_number': s_num})
        
    except Exception as e:
        conn.rollback()
        print(f"Match error: {e}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@settlement_bp.route('/api/settlements/history/<ledger_name>')
@login_required
def get_history(ledger_name):
    try:
        settlements = get_settlements_by_ledger(ledger_name)
        return jsonify(settlements)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@settlement_bp.route('/api/settlements/unmatch/<int:settlement_id>', methods=['POST'])
@login_required
def unmatch(settlement_id):
    try:
        delete_settlement(settlement_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@settlement_bp.route('/api/settlements/<int:settlement_id>/details')
@login_required
def get_settlement_details_api(settlement_id):
    try:
        details = get_settlement_details(settlement_id)
        return jsonify(details)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@settlement_bp.route('/api/settlements/<int:settlement_id>/export')
@login_required
def export_settlement_details(settlement_id):
    try:
        details = get_settlement_details(settlement_id)
        if not details:
            return jsonify({'error': 'No details found'}), 404
            
        # Convert to DataFrame
        df = pd.DataFrame(details)
        
        # Renaissance the columns for better readability if needed, or select specific ones
        # The keys from get_settlement_details are: 
        # assigned_amount, allocation_type, voucher_number, voucher_type, date, narration, original_amount, entry_type
        
        # Select and Rename
        df = df[['voucher_type', 'voucher_number', 'date', 'narration', 'assigned_amount', 'allocation_type']]
        df.columns = ['Voucher Type', 'Voucher No', 'Date', 'Narration', 'Matched Amount', 'Type']
        
        # Create Excel file in memory
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Settlement Details')
            
            # Auto-adjust columns width
            worksheet = writer.sheets['Settlement Details']
            for i, col in enumerate(df.columns):
                column_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
                worksheet.set_column(i, i, column_len)
                
        output.seek(0)
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'Settlement_{settlement_id}_Details.xlsx'
        )
    except Exception as e:
        print(e)
        return jsonify({'error': str(e)}), 500
