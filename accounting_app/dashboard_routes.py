from flask import Blueprint, render_template, session, jsonify, redirect, url_for
from flask_login import login_required, current_user

from database import (
    get_groups,
    get_inventory_groups,
    get_ledger_details,
    get_inventory_details,
)
from database.company_db import get_current_company_id
from . import get_db_connection

dashboard_bp = Blueprint("dashboard_bp", __name__)

def get_dashboard_metrics():
    company_id = get_current_company_id()
    if not company_id:
        return {
            "cash_balance": 0, "bank_balance": 0,
            "sales_count": 0, "sales_amount": 0,
            "sales_return_count": 0, "sales_return_amount": 0,
            "purchase_count": 0, "purchase_amount": 0,
            "purchase_return_count": 0, "purchase_return_amount": 0,
            "receipt_count": 0, "receipt_amount": 0,
            "payment_count": 0, "payment_amount": 0,
            "contra_count": 0, "contra_amount": 0,
            "journal_count": 0, "journal_amount": 0,
            "expense_count": 0, "expense_amount": 0,
            "zero_stock": 0, "stock_warning": 0,
            "stock_critical": 0, "negative_stock": 0
        }

    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT SUM(l.closing_balance) FROM ledgers l "
        "JOIN groups g ON l.group_code = g.group_code AND l.company_id = g.company_id "
        "WHERE g.group_name = 'Cash Accounts' AND l.company_id = ?",
        (company_id,)
    )
    cash_balance = cursor.fetchone()[0] or 0

    cursor.execute(
        "SELECT SUM(l.closing_balance) FROM ledgers l "
        "JOIN groups g ON l.group_code = g.group_code AND l.company_id = g.company_id "
        "WHERE g.group_name = 'Bank Accounts' AND l.company_id = ?",
        (company_id,)
    )
    bank_balance = cursor.fetchone()[0] or 0

    def get_voucher_stats(v_type):
        cursor.execute(
            "SELECT COUNT(*), SUM(amount) FROM vouchers "
            "WHERE voucher_type = ? AND company_id = ?", 
            (v_type, company_id)
        )
        count, amount = cursor.fetchone()
        return count or 0, amount or 0

    sales_count, sales_amount = get_voucher_stats('Sales')
    sales_return_count, sales_return_amount = get_voucher_stats('Sales Return')
    purchase_count, purchase_amount = get_voucher_stats('Purchase')
    purchase_return_count, purchase_return_amount = get_voucher_stats('Purchase Return')
    receipt_count, receipt_amount = get_voucher_stats('Receipt')
    payment_count, payment_amount = get_voucher_stats('Payment')
    contra_count, contra_amount = get_voucher_stats('Contra')
    journal_count, journal_amount = get_voucher_stats('Journal')
    expense_count, expense_amount = get_voucher_stats('Expense')

    cursor.execute(
        "SELECT COUNT(*) FROM inventory WHERE stock_quantity <= 0 AND company_id = ?",
        (company_id,)
    )
    zero_stock = cursor.fetchone()[0] or 0

    cursor.execute(
        "SELECT COUNT(*) FROM inventory "
        "WHERE stock_quantity > 0 AND stock_quantity <= 10 AND company_id = ?",
        (company_id,)
    )
    stock_warning = cursor.fetchone()[0] or 0

    cursor.execute(
        "SELECT COUNT(*) FROM inventory WHERE stock_quantity <= 5 AND company_id = ?",
        (company_id,)
    )
    stock_critical = cursor.fetchone()[0] or 0
    
    cursor.execute(
        "SELECT COUNT(*) FROM inventory WHERE stock_quantity < 0 AND company_id = ?",
        (company_id,)
    )
    negative_stock = cursor.fetchone()[0] or 0

    conn.close()
    
    return {
        "cash_balance": cash_balance,
        "bank_balance": bank_balance,
        "sales_count": sales_count,
        "sales_amount": sales_amount,
        "sales_return_count": sales_return_count,
        "sales_return_amount": sales_return_amount,
        "purchase_count": purchase_count,
        "purchase_amount": purchase_amount,
        "purchase_return_count": purchase_return_count,
        "purchase_return_amount": purchase_return_amount,
        "receipt_count": receipt_count,
        "receipt_amount": receipt_amount,
        "payment_count": payment_count,
        "payment_amount": payment_amount,
        "contra_count": contra_count,
        "contra_amount": contra_amount,
        "journal_count": journal_count,
        "journal_amount": journal_amount,
        "expense_count": expense_count,
        "expense_amount": expense_amount,
        "zero_stock": zero_stock,
        "stock_warning": stock_warning,
        "stock_critical": stock_critical,
        "negative_stock": negative_stock
    }

import json
import os
from flask import current_app, url_for

def get_vite_assets():
    manifest_path = os.path.join(current_app.static_folder, 'dist', '.vite', 'manifest.json')
    try:
        with open(manifest_path, 'r') as f:
            manifest = json.load(f)
        
        entry = manifest.get('src/main.jsx')
        if not entry:
            return ""
            
        js_file = entry.get('file')
        css_files = entry.get('css', [])
        
        tags = []
        if js_file:
            src = url_for('static', filename=f'dist/{js_file}')
            tags.append(f'<script type="module" src="{src}"></script>')
            
        for css_file in css_files:
            href = url_for('static', filename=f'dist/{css_file}')
            tags.append(f'<link rel="stylesheet" href="{href}">')
            
        return "\n".join(tags)
    except Exception as e:
        print(f"Error loading vite manifest: {e}")
        return ""

@dashboard_bp.route("/")
@login_required
def dashboard():
    # Users with the dashboard hidden land on the Vouchers page instead
    if getattr(current_user, "hide_dashboard", False) and not current_user.is_admin:
        return redirect(url_for("voucher_bp.vouchers"))
    assets = get_vite_assets()
    return render_template("react_dashboard.html", username=current_user.username, vite_assets=assets)

@dashboard_bp.route("/dashboard_data")
@login_required
def dashboard_data():
    try:
        metrics = get_dashboard_metrics()
        return jsonify(metrics)
    except Exception as e:
        print(f"Error in dashboard_data: {e}")
        return jsonify({"error": str(e)}), 500
