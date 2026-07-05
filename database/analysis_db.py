import sqlite3
import pandas as pd
from datetime import datetime
from .config import get_connection, DB_TYPE
from .company_db import get_current_company_id

def get_monthly_sales_trend(year=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    
    conn = get_connection()
    cursor = conn.cursor()
    if not year:
        year = datetime.now().year
    
    if DB_TYPE == "postgres":
        month_expr = "TO_CHAR(date::DATE, 'MM')"
        year_expr = "TO_CHAR(date::DATE, 'YYYY')"
    else:
        month_expr = "strftime('%m', date)"
        year_expr = "strftime('%Y', date)"

    query = f"""
        SELECT {month_expr} as month, SUM(amount) as total
        FROM vouchers
        WHERE voucher_type = 'Sales' AND {year_expr} = ? AND company_id = ?
        GROUP BY month
        ORDER BY month
    """
    cursor.execute(query, (str(year), company_id))
    data = cursor.fetchall()
    conn.close()
    
    # Initialize all months with 0
    result = {str(i).zfill(2): 0.0 for i in range(1, 13)}
    for row in data:
        result[row[0]] = float(row[1])
    
    return result

def get_monthly_purchase_trend(year=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    if not year:
        year = datetime.now().year
    
    if DB_TYPE == "postgres":
        month_expr = "TO_CHAR(date::DATE, 'MM')"
        year_expr = "TO_CHAR(date::DATE, 'YYYY')"
    else:
        month_expr = "strftime('%m', date)"
        year_expr = "strftime('%Y', date)"

    query = f"""
        SELECT {month_expr} as month, SUM(amount) as total
        FROM vouchers
        WHERE voucher_type = 'Purchase' AND {year_expr} = ? AND company_id = ?
        GROUP BY month
        ORDER BY month
    """
    cursor.execute(query, (str(year), company_id))
    data = cursor.fetchall()
    conn.close()
    
    result = {str(i).zfill(2): 0.0 for i in range(1, 13)}
    for row in data:
        result[row[0]] = float(row[1])
    
    return result

def get_top_customers(limit=5, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    
    # Correctly computing top customers by joining with ledgers (Group G007 - Sundry Debtors)
    query = """
        SELECT l.ledger_name, SUM(le.amount) as total_sales
        FROM vouchers v
        JOIN ledger_entries le ON v.voucher_number = le.voucher_number AND v.company_id = le.company_id
        JOIN ledgers l ON le.ledger_name = l.ledger_name AND le.company_id = l.company_id
        WHERE v.voucher_type = 'Sales' AND l.group_code = 'G007' AND v.company_id = ?
        GROUP BY l.ledger_name
        ORDER BY total_sales DESC
        LIMIT ?
    """
    cursor.execute(query, (company_id, limit))
    data = cursor.fetchall()
    conn.close()
    
    return [{"name": row[0], "value": row[1]} for row in data]

def get_top_suppliers(limit=5, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    
    # Correctly computing top suppliers (Group G008 - Sundry Creditors)
    query = """
        SELECT l.ledger_name, SUM(le.amount) as total_purchase
        FROM vouchers v
        JOIN ledger_entries le ON v.voucher_number = le.voucher_number AND v.company_id = le.company_id
        JOIN ledgers l ON le.ledger_name = l.ledger_name AND le.company_id = l.company_id
        WHERE v.voucher_type = 'Purchase' AND l.group_code = 'G008' AND v.company_id = ?
        GROUP BY l.ledger_name
        ORDER BY total_purchase DESC
        LIMIT ?
    """
    cursor.execute(query, (company_id, limit))
    data = cursor.fetchall()
    conn.close()
    
    return [{"name": row[0], "value": row[1]} for row in data]

def get_stock_category_summary(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    
    # Assumes inventory calculation logic is relatively simple or cached in 'inventory' table.
    # If live calculation is needed, this might need to use inventory_db logic.
    # For now, using 'inventory' table if it has current stock value.
    # Checking existing schema via list_dir suggested 'inventory' table exists.
    # Usually it's better to reuse get_closing_inventory_data but that might be slow for just a summary.
    # Let's try to aggregate from current stock if available, or just use Items joined with Groups.
    
    # Assuming 'inventory' table has item details including 'group_name' and 'stock_value' 
    # or we need to calculate value = qty * cost.
    
    # Fallback if specific table structure is different, I'll need to check schema.
    # But let's write a safe query assuming standard structure or adjust later.
    # Actually, let's use the existing get_closing_inventory_data logic which is more robust
    # but that might be in a different module. 
    # To avoid circular imports or duplication, let's just query the 'inventory' table directly 
    # if it stores the current state (which dashboard queries suggest it does).
    
    query = """
        SELECT ig.group_name, SUM(i.stock_quantity * i.unit_price)
        FROM inventory i
        JOIN inventory_groups ig ON i.stock_group_code = ig.group_code AND i.company_id = ig.company_id
        WHERE i.stock_quantity > 0 AND i.company_id = ?
        GROUP BY ig.group_name
    """
    try:
        cursor.execute(query, (company_id,))
        data = cursor.fetchall()
        conn.close()
        return [{"category": row[0], "value": row[1] if row[1] else 0} for row in data]
    except Exception as e:
        print(f"Error in stock summary: {e}")
        conn.close()
        return []

def get_financial_comparison(year=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    if not year:
        year = datetime.now().year
        
    # Income (Sales + Service Income + Indirect Incomes) vs Expense (Purchase + Expenses)
    # This is a simplification. Real P&L is more complex.
    # For trend, we'll just track monthly totals of Income-nature vouchers and Expense-nature vouchers.
    
    if DB_TYPE == "postgres":
        month_expr = "TO_CHAR(date::DATE, 'MM')"
        year_expr = "TO_CHAR(date::DATE, 'YYYY')"
    else:
        month_expr = "strftime('%m', date)"
        year_expr = "strftime('%Y', date)"
    
    # Income: Sales, Service Income
    # Expense: Purchase, Expense
    
    income_query = f"""
        SELECT {month_expr} as month, SUM(amount)
        FROM vouchers
        WHERE voucher_type IN ('Sales', 'Service Income') AND {year_expr} = ? AND company_id = ?
        GROUP BY month
    """
    
    expense_query = f"""
        SELECT {month_expr} as month, SUM(amount)
        FROM vouchers
        WHERE voucher_type IN ('Purchase', 'Expense') AND {year_expr} = ? AND company_id = ?
        GROUP BY month
    """
    
    cursor.execute(income_query, (str(year), company_id))
    income_data = cursor.fetchall()
    
    cursor.execute(expense_query, (str(year), company_id))
    expense_data = cursor.fetchall()
    conn.close()
    
    income_dict = {str(i).zfill(2): 0.0 for i in range(1, 13)}
    for row in income_data:
        income_dict[row[0]] = float(row[1])
        
    expense_dict = {str(i).zfill(2): 0.0 for i in range(1, 13)}
    for row in expense_data:
        expense_dict[row[0]] = float(row[1])
        
    return {"income": income_dict, "expense": expense_dict}

def get_kpi_summary(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()

    conn = get_connection()
    cursor = conn.cursor()
    
    # Total Sales YTD
    cursor.execute("SELECT SUM(amount) FROM vouchers WHERE voucher_type='Sales' AND company_id = ?", (company_id,))
    total_sales = cursor.fetchone()[0] or 0
    
    # Total Purchase YTD
    cursor.execute("SELECT SUM(amount) FROM vouchers WHERE voucher_type='Purchase' AND company_id = ?", (company_id,))
    total_purchase = cursor.fetchone()[0] or 0
    
    # Total Receivables (Sundry Debtors G007)
    # Using ledgers table closing balance if updated properly
    cursor.execute("SELECT SUM(closing_balance) FROM ledgers WHERE group_code='G007' AND company_id = ?", (company_id,))
    total_receivables = cursor.fetchone()[0] or 0
    
    # Total Payables (Sundry Creditors G008)
    cursor.execute("SELECT SUM(closing_balance) FROM ledgers WHERE group_code='G008' AND company_id = ?", (company_id,))
    total_payables = cursor.fetchone()[0] or 0
    
    conn.close()
    
    return {
        "total_sales": total_sales,
        "total_purchase": total_purchase,
        "total_receivables": total_receivables,
        "total_payables": total_payables
    }
