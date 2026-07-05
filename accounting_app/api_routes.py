from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
import sqlite3
from datetime import datetime

from database import (
    get_monthly_sales_trend,
    get_monthly_purchase_trend,
    get_top_customers,
    get_top_suppliers,
    get_kpi_summary,
    get_financial_comparison,
    get_closing_inventory_data,
    get_balance_sheet_data,
    get_slow_moving_items,
    get_company_settings
)
from . import get_db_connection

api_bp = Blueprint("api_bp", __name__)

@api_bp.route("/api/company-settings")
@login_required
def api_company_settings():
    try:
        settings = get_company_settings()
        if settings:
            return jsonify({
                "currency_code": settings.get("currency_code", "AED"),
                "company_name": settings.get("company_name", "")
            })
        return jsonify({"error": "Company settings not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/api/analysis/kpi-summary")
@login_required
def api_kpi_summary():
    try:
        data = get_kpi_summary()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/api/analysis/sales-trend")
@login_required
def api_sales_trend():
    year = request.args.get('year', datetime.now().year)
    try:
        data = get_monthly_sales_trend(year)
        # Convert dictionary to list for Recharts
        chart_data = [{"name": k, "value": v} for k, v in data.items()]
        return jsonify(chart_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/api/analysis/top-customers")
@login_required
def api_top_customers():
    try:
        data = get_top_customers(10)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/api/analysis/financial-comparison")
@login_required
def api_financial_comparison():
    year = request.args.get('year', datetime.now().year)
    try:
        data = get_financial_comparison(year)
        # Transform for chart: [{month: '01', income: 100, expense: 50}, ...]
        chart_data = []
        for month in sorted(data['income'].keys()):
            chart_data.append({
                "month": month,
                "Income": data['income'][month],
                "Expense": data['expense'][month]
            })
        return jsonify(chart_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/api/analysis/inventory-summary")
@login_required
def api_inventory_summary():
    try:
        # Get slow moving items count
        slow_moving = len(get_slow_moving_items(90))
        
        # Get stock value by group
        inventory, total_val = get_closing_inventory_data(None)
        
        # Override total_val with Balance Sheet Inventory Value to ensure consistency
        # as per user requirement.
        bs_data, total_assets, total_liabilities = get_balance_sheet_data(None)
        bs_inventory_val = 0.0
        if 'assets' in bs_data:
             for group, assets in bs_data['assets'].items():
                for asset in assets:
                    # Heuristic to match inventory/stock ledgers in Assets
                    ledger_name = asset.get('ledger_name', '').lower()
                    if 'inventory' in ledger_name or 'stock' in ledger_name or 'stock-in-hand' in group.lower():
                         bs_inventory_val += asset.get('amount', 0.0)
        
        # Use the balance sheet value if it's non-zero, or if we want to enforce it strictly.
        # Given the request "It should use same data of balancesheet", we use it.
        # However, if BS value is 0 and Inventory is not, it might be weird. 
        # But usually BS is the source of truth for financial reports.
        total_val = bs_inventory_val

        group_summary = {}
        for item in inventory:
            group = item.get('group_name', 'Unknown')
            group_summary[group] = group_summary.get(group, 0) + float(item.get('cost_amount', 0))
            
        chart_data = [{"name": k, "value": v} for k, v in group_summary.items()]
        
        return jsonify({
            "slow_moving_count": slow_moving,
            "total_stock_value": total_val,
            "category_distribution": chart_data
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
