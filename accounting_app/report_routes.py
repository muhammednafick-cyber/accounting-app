from flask import Blueprint, render_template, request, jsonify, send_file
from flask_login import login_required, current_user
import sqlite3
from datetime import datetime
import pandas as pd
import io

from database import (
    get_ledgers,
    get_items,
    get_groups,
    get_ledger_transactions,
    get_trial_balance_data,
    get_stock_movement_data,
    get_balance_sheet_data,
    get_profit_and_loss_data,
    get_closing_inventory_data,
    get_ageing_report_data,
    get_voucher_register_data,
    get_sales_summary_data,
    get_purchase_summary_data,
    get_vat_summary_data,
    get_vat_detailed_report_data,
    get_slow_moving_items,
    get_cash_flow_data,
    get_negative_stock_items,
    get_monthly_sales_trend,
    get_monthly_purchase_trend,
    get_top_customers,
    get_top_suppliers,
    get_stock_category_summary,
    get_financial_comparison,
    get_kpi_summary,
)
from .models import format_date, parse_date
from . import get_db_connection

report_bp = Blueprint("report_bp", __name__)


@report_bp.route("/report")
@login_required
def report():
    return render_template("report.html", username=current_user.username)


@report_bp.route("/reports/all")
@login_required
def report_all():
    return render_template("report_all.html", username=current_user.username)


@report_bp.route("/report/ledger-transactions")
@login_required
def report_ledger_transactions():
    ledgers = get_ledgers()
    print(f"Ledger transactions report - Ledgers: {ledgers}")
    return render_template(
        "report_ledger_transactions.html",
        ledgers=ledgers,
        report_title="General Ledger Transactions",
        username=current_user.username,
    )

@report_bp.route("/report/customer-ledger")
@login_required
def report_customer_ledger():
    ledgers = get_ledgers(group_code='G007')
    return render_template(
        "report_ledger_transactions.html",
        ledgers=ledgers,
        report_title="Customer Ledger",
        username=current_user.username,
    )

@report_bp.route("/report/supplier-ledger")
@login_required
def report_supplier_ledger():
    ledgers = get_ledgers(group_code='G008')
    return render_template(
        "report_ledger_transactions.html",
        ledgers=ledgers,
        report_title="Supplier Ledger",
        username=current_user.username,
    )

@report_bp.route("/report/bank-book")
@login_required
def report_bank_book():
    ledgers = get_ledgers(group_code='G006')
    return render_template(
        "report_ledger_transactions.html",
        ledgers=ledgers,
        report_title="Bank Book",
        username=current_user.username,
    )

@report_bp.route("/report/cash-book")
@login_required
def report_cash_book():
    ledgers = get_ledgers(group_code='G005')
    return render_template(
        "report_ledger_transactions.html",
        ledgers=ledgers,
        report_title="Cash Book",
        username=current_user.username,
    )

@report_bp.route("/report/cash-flow")
@login_required
def report_cash_flow():
    return render_template("report_cash_flow.html", username=current_user.username)

@report_bp.route("/report/register/<voucher_type>")
@login_required
def report_register(voucher_type):
    return render_template("report_register.html", voucher_type=voucher_type, username=current_user.username)

@report_bp.route("/report/daybook")
@login_required
def report_daybook():
    return render_template("report_daybook.html", username=current_user.username)

@report_bp.route("/report/sales-summary")
@login_required
def report_sales_summary():
    return render_template("report_sales_summary.html", username=current_user.username)

@report_bp.route("/report/purchase-summary")
@login_required
def report_purchase_summary():
    return render_template("report_purchase_summary.html", username=current_user.username)

@report_bp.route("/report/stock-summary")
@login_required
def report_stock_summary():
    return render_template("report_stock_summary.html", username=current_user.username)

@report_bp.route("/report/stock-valuation")
@login_required
def report_stock_valuation():
    return render_template("report_stock_valuation.html", username=current_user.username)

@report_bp.route("/report/slow-moving")
@login_required
def report_slow_moving():
    return render_template("report_slow_moving.html", username=current_user.username)


@report_bp.route("/report/negative-stock")
@login_required
def report_negative_stock():
    items = get_negative_stock_items()
    return render_template(
        "report_negative_stock.html", items=items, username=current_user.username
    )

@report_bp.route("/report/vat-summary")
@login_required
def report_vat_summary():
    return render_template("report_vat_summary.html", username=current_user.username)

@report_bp.route("/report/vat-detailed")
@login_required
def report_vat_detailed():
    return render_template("report_vat_detailed.html", username=current_user.username)


@report_bp.route("/report/vat_expense")
@login_required
def report_vat_expense():
    return render_template("report_vat_expense.html", username=current_user.username)


@report_bp.route("/report/trial-balance")
@login_required
def report_trial_balance():
    ledgers = get_ledgers()
    print(f"Trial balance report - Ledgers: {ledgers}")
    return render_template(
        "report_trial_balance.html",
        ledgers=ledgers,
        username=current_user.username,
    )


@report_bp.route("/report/stock-movement")
@login_required
def report_stock_movement():
    items = get_items()
    print(f"Stock movement report - Items: {items}")
    return render_template(
        "report_stock_movement.html",
        items=items,
        username=current_user.username,
    )


@report_bp.route("/report/balance-sheet")
@login_required
def report_balance_sheet():
    return render_template(
        "report_balance_sheet.html", username=current_user.username
    )


@report_bp.route("/report/profit-and-loss")
@login_required
def report_profit_and_loss():
    return render_template(
        "report_profit_and_loss.html", username=current_user.username
    )


@report_bp.route("/report/closing-inventory")
@login_required
def report_closing_inventory():
    return render_template(
        "report_closing_inventory.html", username=current_user.username
    )


@report_bp.route("/get_transactions")
@login_required
def get_transactions():
    ledger_name = request.args.get("ledger_name")
    from_date = parse_date(request.args.get("from_date"))
    to_date = parse_date(request.args.get("to_date"))
    try:
        transactions, closing_balance = get_ledger_transactions(
            ledger_name, from_date, to_date
        )
        return jsonify({
            "transactions": [
                {
                    "date": format_date(t["date"]) if t.get("date") else "",
                    "voucher_number": t.get("voucher_number", ""),
                    "voucher_type": t.get("voucher_type", ""),
                    "narration": t.get("narration", ""),
                    "debit": t.get("debit", 0),
                    "credit": t.get("credit", 0),
                    "balance": t.get("balance", 0),
                }
                for t in transactions
            ],
            "closing_balance": closing_balance,
        })
    except Exception as e:
        print(f"Error in get_transactions: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500

@report_bp.route("/get_cash_flow")
@login_required
def get_cash_flow():
    from_date = parse_date(request.args.get("from_date"))
    to_date = parse_date(request.args.get("to_date"))
    try:
        data = get_cash_flow_data(from_date, to_date)
        return jsonify({"cash_flow": data})
    except sqlite3.Error as e:
        print(f"Error in get_cash_flow: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@report_bp.route("/get_register_data")
@login_required
def get_register_data_api():
    voucher_type = request.args.get("voucher_type", "All")
    from_date = parse_date(request.args.get("from_date"))
    to_date = parse_date(request.args.get("to_date"))
    
    vouchers = get_voucher_register_data(voucher_type, from_date, to_date)
    formatted_vouchers = []
    for v in vouchers:
        v['date'] = format_date(v['date'])
        formatted_vouchers.append(v)
    return jsonify({'vouchers': formatted_vouchers})


@report_bp.route("/get_sales_summary")
@login_required
def get_sales_summary():
    from_date = parse_date(request.args.get("from_date"))
    to_date = parse_date(request.args.get("to_date"))
    try:
        data = get_sales_summary_data(from_date, to_date)
        return jsonify({"summary": data})
    except sqlite3.Error as e:
        print(f"Error in get_sales_summary: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@report_bp.route("/get_purchase_summary")
@login_required
def get_purchase_summary():
    from_date = parse_date(request.args.get("from_date"))
    to_date = parse_date(request.args.get("to_date"))
    try:
        data = get_purchase_summary_data(from_date, to_date)
        return jsonify({"summary": data})
    except sqlite3.Error as e:
        print(f"Error in get_purchase_summary: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@report_bp.route("/get_vat_summary")
@login_required
def get_vat_summary():
    from_date = parse_date(request.args.get("from_date"))
    to_date = parse_date(request.args.get("to_date"))
    try:
        data = get_vat_summary_data(from_date, to_date)
        # Frontend expects { summary: { ... } }
        return jsonify({"summary": data})
    except sqlite3.Error as e:
        print(f"Error in get_vat_summary: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@report_bp.route("/get_slow_moving")
@login_required
def get_slow_moving():
    days = request.args.get("days", 90)
    try:
        data = get_slow_moving_items(days)
        # Format last_sale_date
        for item in data:
            if item.get('last_sale_date') and item['last_sale_date'] != 'Never':
                item['last_sale_date'] = format_date(item['last_sale_date'])
        return jsonify({"items": data})
    except sqlite3.Error as e:
        print(f"Error in get_slow_moving: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@report_bp.route("/report/ageing")
@login_required
def report_ageing():
    return render_template("report_ageing.html", username=current_user.username)

@report_bp.route("/get_ageing_report")
@login_required
def get_ageing_report():
    report_type = request.args.get("type", "receivable") # receivable or payable
    as_of_date = parse_date(request.args.get("as_of_date"))
    
    # G007 = Debtors (Receivable), G008 = Creditors (Payable)
    group_code = 'G007' if report_type == 'receivable' else 'G008'
    
    try:
        data = get_ageing_report_data(group_code, as_of_date)
        return jsonify({"success": True, "data": data})
    except Exception as e:
        print(f"Error in get_ageing_report: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@report_bp.route("/get_trial_balance")
@login_required
def get_trial_balance():
    as_of_date = parse_date(request.args.get("as_of_date"))
    try:
        trial_balance, total_debit, total_credit = get_trial_balance_data(
            as_of_date
        )
        print(
            f"Trial balance as of {as_of_date}: "
            f"total_debit={total_debit}, total_credit={total_credit}"
        )
        return jsonify(
            {
                "trial_balance": trial_balance,
                "total_debit": total_debit,
                "total_credit": total_credit,
            }
        )
    except sqlite3.Error as e:
        print(f"Error in get_trial_balance: {str(e)}")
        return (
            jsonify({"success": False, "message": str(e)}),
            500,
        )


@report_bp.route("/get_stock_movement")
@login_required
def get_stock_movement():
    item_name = request.args.get("item_name")
    from_date = parse_date(request.args.get("from_date"))
    to_date = parse_date(request.args.get("to_date"))
    try:
        stock_movement = get_stock_movement_data(item_name, from_date, to_date)
        formatted_movement = []
        for m in stock_movement:
            fm = list(m)
            fm[1] = format_date(fm[1])
            formatted_movement.append(fm)
        print(f"Stock movement for {item_name}: {len(stock_movement)} entries")
        return jsonify({"stock_movement": formatted_movement})
    except sqlite3.Error as e:
        print(f"Error in get_stock_movement: {str(e)}")
        return (
            jsonify({"success": False, "message": str(e)}),
            500,
        )


@report_bp.route("/get_balance_sheet")
@login_required
def get_balance_sheet():
    as_of_date = parse_date(request.args.get("as_of_date"))
    try:
        balance_sheet, total_assets, total_liabilities = (
            get_balance_sheet_data(as_of_date)
        )
        print(
            f"Balance sheet as of {as_of_date}: "
            f"total_assets={total_assets}, "
            f"total_liabilities={total_liabilities}"
        )
        return jsonify(
            {
                "balance_sheet": balance_sheet,
                "total_assets": total_assets,
                "total_liabilities": total_liabilities,
            }
        )
    except sqlite3.Error as e:
        print(f"Error in get_balance_sheet: {str(e)}")
        return (
            jsonify({"success": False, "message": str(e)}),
            500,
        )


@report_bp.route("/get_profit_and_loss")
@login_required
def get_profit_and_loss():
    from_date = parse_date(request.args.get("from_date"))
    to_date = parse_date(request.args.get("to_date"))
    try:
        (
            profit_and_loss,
            total_income,
            total_expenses,
            net_profit,
        ) = get_profit_and_loss_data(from_date, to_date)
        print(
            f"Profit and loss from {from_date} to {to_date}: "
            f"net_profit={net_profit}"
        )
        return jsonify(
            {
                "profit_and_loss": profit_and_loss,
                "total_income": total_income,
                "total_expenses": total_expenses,
                "net_profit": net_profit,
            }
        )
    except sqlite3.Error as e:
        print(f"Error in get_profit_and_loss: {str(e)}")
        return (
            jsonify({"success": False, "message": str(e)}),
            500,
        )


@report_bp.route("/get_closing_inventory")
@login_required
def get_closing_inventory():
    as_of_date = parse_date(request.args.get("as_of_date"))
    try:
        (
            closing_inventory,
            total_cost_amount,
        ) = get_closing_inventory_data(as_of_date)
        print(
            f"Closing inventory as of {as_of_date}: "
            f"total_cost_amount={total_cost_amount}"
        )
        return jsonify(
            {
                "closing_inventory": closing_inventory,
                "total_cost_amount": total_cost_amount,
            }
        )
    except sqlite3.Error as e:
        print(f"Error in get_closing_inventory: {str(e)}")
        return (
            jsonify({"success": False, "message": str(e)}),
            500,
        )

def create_excel_response(df, filename):
    output = io.BytesIO()
    # Use xlsxwriter as engine
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Sheet1')
        # Auto-adjust columns width
        workbook = writer.book
        worksheet = writer.sheets['Sheet1']
        for i, col in enumerate(df.columns):
            # Handle various types for length calculation
            max_len = len(str(col))
            for item in df[col]:
                try:
                    if item:
                         max_len = max(max_len, len(str(item)))
                except:
                    pass
            
            # Limit max width to 50
            final_len = min(max_len + 2, 50)
            worksheet.set_column(i, i, final_len)
            
    output.seek(0)
    
    return send_file(
        output,
        download_name=f"{filename}.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@report_bp.route("/export_report/<report_type>")
@login_required
def export_report(report_type):
    from_date = parse_date(request.args.get("from_date"))
    to_date = parse_date(request.args.get("to_date"))
    as_of_date = parse_date(request.args.get("as_of_date")) or to_date
    
    try:
        if report_type == "ledger_transactions":
            ledger_name = request.args.get("ledger_name")
            transactions, closing_balance = get_ledger_transactions(ledger_name, from_date, to_date)
            formatted_data = [
                {
                    "Voucher Number": t[0],
                    "Voucher Type": t[1],
                    "Date": format_date(t[2]),
                    "Debit": t[3],
                    "Credit": t[4],
                    "Running Balance": t[5]
                }
                for t in transactions
            ]
            formatted_data.append({
                "Voucher Number": "Closing Balance",
                "Voucher Type": "",
                "Date": "",
                "Debit": "",
                "Credit": "",
                "Running Balance": closing_balance
            })
            return create_excel_response(pd.DataFrame(formatted_data), f"{ledger_name}_{from_date}_{to_date}")

        elif report_type == "register":
            voucher_type = request.args.get("voucher_type", "All")
            vouchers = get_voucher_register_data(voucher_type, from_date, to_date)
            formatted_data = []
            
            # Registers that show a VAT Amount column
            vat_register_types = ['Sales', 'Sales Return', 'Purchase', 'Purchase Return', 'Expense']

            if voucher_type in ['Sales', 'Purchase', 'Sales Return', 'Purchase Return']:
                # Itemwise breakdown
                for v in vouchers:
                    if not v.get('items'):
                        formatted_data.append({
                            "Date": format_date(v['date']),
                            "Voucher Type": v['voucher_type'],
                            "Voucher Number": v['voucher_number'],
                            "Party": v['party_name'],
                            "Item": "",
                            "Qty": "",
                            "Unit rate": "",
                            # Voucher total excluding VAT
                            "Amount": (v['amount'] or 0) - v.get('vat_amount', 0),
                            "VAT Amount": v.get('vat_amount', 0),
                            "Narration": v['narration']
                        })
                    else:
                        for idx, item in enumerate(v['items']):
                            formatted_data.append({
                                "Date": format_date(v['date']),
                                "Voucher Type": v['voucher_type'],
                                "Voucher Number": v['voucher_number'],
                                "Party": v['party_name'],
                                "Item": item['name'],
                                "Qty": item['qty'],
                                "Unit rate": item['rate'],
                                "Amount": item['amount'],
                                # VAT is voucher-level; show on first item row only to avoid double counting
                                "VAT Amount": v.get('vat_amount', 0) if idx == 0 else "",
                                "Narration": v['narration']
                            })
            else:
                for v in vouchers:
                    items_str = ", ".join([f"{i['name']} ({i['qty']})" for i in v.get('items', [])])
                    row = {
                        "Date": format_date(v['date']),
                        "Voucher Type": v['voucher_type'],
                        "Voucher Number": v['voucher_number'],
                        "Party": v['party_name'],
                        "Amount": v['amount'],
                        "Narration": v['narration'],
                        "Items": items_str
                    }
                    if voucher_type in vat_register_types:
                        # Amount excluding VAT, with VAT shown separately
                        row["Amount"] = (v['amount'] or 0) - v.get('vat_amount', 0)
                        row["VAT Amount"] = v.get('vat_amount', 0)
                    formatted_data.append(row)
            return create_excel_response(pd.DataFrame(formatted_data), f"{voucher_type}_Register_{from_date}_{to_date}")

        elif report_type == "daybook":
            # Daybook uses register data for a single day (passed as from_date and to_date in existing logic)
            # The template sends from_date=date and to_date=date
            vouchers = get_voucher_register_data("All", from_date, to_date)
            formatted_data = []
            for v in vouchers:
                items_str = ", ".join([f"{i['name']} ({i['qty']})" for i in v.get('items', [])])
                formatted_data.append({
                    "Date": format_date(v['date']),
                    "Voucher Type": v['voucher_type'],
                    "Voucher Number": v['voucher_number'],
                    "Party": v['party_name'],
                    "Amount": v['amount'],
                    "Narration": v['narration'],
                    "Items": items_str
                })
            return create_excel_response(pd.DataFrame(formatted_data), f"Daybook_{from_date}")

        elif report_type == "stock_movement":
            item_name = request.args.get("item_name")
            stock_movement = get_stock_movement_data(item_name, from_date, to_date)
            formatted_data = [
                {
                    "Voucher Number": m[0],
                    "Date": format_date(m[1]),
                    "Voucher Type": m[2],
                    "Location": m[8] if len(m) > 8 else "",
                    "Qty In": m[3],
                    "Qty Out": m[4],
                    "Balance Qty": m[5],
                    "WAP": m[6],
                    "Balance Value": m[7]
                }
                for m in stock_movement
            ]
            return create_excel_response(pd.DataFrame(formatted_data), f"Stock_Movement_{item_name}_{from_date}_{to_date}")

        elif report_type == "sales_summary":
            data = get_sales_summary_data(from_date, to_date)
            return create_excel_response(pd.DataFrame(data), f"Sales_Summary_{from_date}_{to_date}")

        elif report_type == "purchase_summary":
            data = get_purchase_summary_data(from_date, to_date)
            return create_excel_response(pd.DataFrame(data), f"Purchase_Summary_{from_date}_{to_date}")

        elif report_type == "stock_summary":
            inventory, total_val = get_closing_inventory_data(as_of_date)
            summary = {}
            for item in inventory:
                group = item.get('group_name', 'Unknown')
                if group not in summary:
                    summary[group] = {'qty': 0.0, 'val': 0.0}
                summary[group]['qty'] += float(item.get('quantity', 0))
                summary[group]['val'] += float(item.get('cost_amount', 0))
            
            formatted_data = [
                {"Category": k, "Total Quantity": v['qty'], "Total Value": v['val']}
                for k, v in summary.items()
            ]
            formatted_data.append({"Category": "Grand Total", "Total Quantity": "", "Total Value": total_val})
            return create_excel_response(pd.DataFrame(formatted_data), f"Stock_Summary_{as_of_date}")

        elif report_type == "closing_inventory":
            inventory, total_val = get_closing_inventory_data(as_of_date)
            return create_excel_response(pd.DataFrame(inventory), f"Closing_Inventory_{as_of_date}")

        elif report_type == "trial_balance":
            data, total_dr, total_cr = get_trial_balance_data(as_of_date)
            formatted_data = list(data)
            formatted_data.append({
                "ledger_name": "Total",
                "group_name": "",
                "debit": total_dr,
                "credit": total_cr
            })
            return create_excel_response(pd.DataFrame(formatted_data), f"Trial_Balance_{as_of_date}")

        elif report_type == "balance_sheet":
            bs_data, tot_assets, tot_liab = get_balance_sheet_data(as_of_date)
            rows = []
            rows.append({"Section": "ASSETS", "Group": "", "Amount": ""})
            for item in bs_data.get('Assets', []):
                 rows.append({"Section": "", "Group": item['group_name'], "Amount": item['amount']})
            rows.append({"Section": "Total Assets", "Group": "", "Amount": tot_assets})
            rows.append({"Section": "", "Group": "", "Amount": ""})
            rows.append({"Section": "LIABILITIES", "Group": "", "Amount": ""})
            for item in bs_data.get('Liabilities', []):
                 rows.append({"Section": "", "Group": item['group_name'], "Amount": item['amount']})
            rows.append({"Section": "Total Liabilities", "Group": "", "Amount": tot_liab})
            return create_excel_response(pd.DataFrame(rows), f"Balance_Sheet_{as_of_date}")

        elif report_type == "profit_and_loss":
            pl_data, tot_inc, tot_exp, net_profit = get_profit_and_loss_data(from_date, to_date)
            rows = []
            rows.append({"Section": "INCOME", "Group": "", "Amount": ""})
            for item in pl_data.get('Income', []):
                rows.append({"Section": "", "Group": item['group_name'], "Amount": item['amount']})
            rows.append({"Section": "Total Income", "Group": "", "Amount": tot_inc})
            rows.append({"Section": "", "Group": "", "Amount": ""})
            rows.append({"Section": "EXPENSES", "Group": "", "Amount": ""})
            for item in pl_data.get('Expenses', []):
                rows.append({"Section": "", "Group": item['group_name'], "Amount": item['amount']})
            rows.append({"Section": "Total Expenses", "Group": "", "Amount": tot_exp})
            rows.append({"Section": "", "Group": "", "Amount": ""})
            rows.append({"Section": "NET PROFIT", "Group": "", "Amount": net_profit})
            return create_excel_response(pd.DataFrame(rows), f"Profit_Loss_{from_date}_{to_date}")
            
        elif report_type == "vat_summary":
            vat_data = get_vat_summary_data(from_date, to_date)
            df = pd.DataFrame([vat_data])
            return create_excel_response(df, f"VAT_Summary_{from_date}_{to_date}")

        elif report_type == "vat_detailed":
            data = get_vat_detailed_report_data(from_date, to_date)
            
            rows = []

            def _blank_row(section=""):
                return {"Section": section, "Date": "", "Voucher No": "", "Invoice Date": "", "Party": "",
                        "Description": "", "Taxable": "", "VAT": "", "Total": ""}

            def _data_row(row):
                return {
                    "Section": "",
                    "Date": format_date(row['date']),
                    "Voucher No": row['voucher_number'],
                    "Invoice Date": format_date(row['invoice_date']) if row.get('invoice_date') else "",
                    "Party": row['party_name'],
                    "Description": row.get('narration') or "",
                    "Taxable": round(row['taxable'], 2),
                    "VAT": round(row['vat'], 2),
                    "Total": round(row['total'], 2)
                }

            # OUTPUT VAT SECTION
            rows.append(_blank_row("OUTPUT VAT (SALES)"))
            for row in data['output_rows']:
                rows.append(_data_row(row))
            total_row = _blank_row("Total Output VAT")
            total_row["VAT"] = round(data['total_output_vat'], 2)
            rows.append(total_row)

            rows.append(_blank_row())

            # INPUT VAT SECTION
            rows.append(_blank_row("INPUT VAT (PURCHASE / EXPENSE)"))
            for row in data['input_rows']:
                rows.append(_data_row(row))
            total_row = _blank_row("Total Input VAT")
            total_row["VAT"] = round(data['total_input_vat'], 2)
            rows.append(total_row)

            rows.append(_blank_row())

            net_vat = data['total_output_vat'] - data['total_input_vat']
            net_row = _blank_row("NET VAT PAYABLE")
            net_row["VAT"] = round(net_vat, 2)
            rows.append(net_row)

            return create_excel_response(pd.DataFrame(rows), f"VAT_Detailed_{from_date}_{to_date}")

        elif report_type == "vat_expense":
            data = get_vat_detailed_report_data(from_date, to_date)

            rows = []
            total_vat = 0.0
            for row in data['input_rows']:
                if row.get('voucher_type') != 'Expense':
                    continue
                rows.append({
                    "Date": format_date(row['date']),
                    "Voucher No": row['voucher_number'],
                    "Invoice Date": format_date(row['invoice_date']) if row.get('invoice_date') else "",
                    "Invoice Ref": row.get('invoice_ref') or "",
                    "Party": row['party_name'],
                    "Description": row.get('narration') or "",
                    "Taxable": round(row['taxable'], 2),
                    "VAT": round(row['vat'], 2),
                    "Total": round(row['total'], 2)
                })
                total_vat += row['vat']
            rows.append({
                "Date": "", "Voucher No": "", "Invoice Date": "", "Invoice Ref": "",
                "Party": "", "Description": "Total Expense VAT",
                "Taxable": "", "VAT": round(total_vat, 2), "Total": ""
            })

            return create_excel_response(pd.DataFrame(rows), f"VAT_Expense_{from_date}_{to_date}")

        elif report_type == "slow_moving":
            days = request.args.get("days", 90)
            data = get_slow_moving_items(days)
            return create_excel_response(pd.DataFrame(data), f"Slow_Moving_Stock_{days}_days")

        elif report_type == "stock_movement":
            item_name = request.args.get("item_name")
            data = get_stock_movement_data(item_name, from_date, to_date)
            formatted_data = []
            for row in data:
                formatted_data.append({
                    "Voucher No": row[0],
                    "Date": format_date(row[1]),
                    "Type": row[2],
                    "Location": row[8] if len(row) > 8 else "",
                    "In Qty": row[3],
                    "Out Qty": row[4],
                    "Balance Qty": row[5],
                    "WAP": row[6],
                    "Balance Value": row[7]
                })
            return create_excel_response(pd.DataFrame(formatted_data), f"Stock_Movement_{item_name}_{from_date}_{to_date}")

        elif report_type == "ageing":
            report_sub_type = request.args.get("type", "receivable")
            # G007 = Debtors (Receivable), G008 = Creditors (Payable)
            group_code = 'G007' if report_sub_type == 'receivable' else 'G008'
            data = get_ageing_report_data(group_code, as_of_date)
            
            formatted_data = []
            for item in data:
                formatted_data.append({
                    "Party Name": item['ledger_name'],
                    "Balance": item['balance'],
                    "Not Due": item['buckets']['not_due'],
                    "0-30 Days": item['buckets']['0_30'],
                    "31-60 Days": item['buckets']['31_60'],
                    "61-90 Days": item['buckets']['61_90'],
                    "> 90 Days": item['buckets']['90_plus']
                })
            return create_excel_response(pd.DataFrame(formatted_data), f"Ageing_Report_{report_sub_type}_{as_of_date}")

        elif report_type == "cash_flow":
            cf = get_cash_flow_data(from_date, to_date)
            rows = []
            
            rows.append({"Particulars": "Cash Flow from Operating Activities", "Amount": cf['operating']})
            rows.append({"Particulars": "  Net Profit before Tax", "Amount": cf['details']['net_profit']})
            
            if abs(cf['details'].get('depreciation', 0)) > 0.001:
                rows.append({"Particulars": "  Add: Depreciation", "Amount": cf['details']['depreciation']})
                
            rows.append({"Particulars": "  Operating Profit before WC Changes", "Amount": cf['details'].get('operating_profit', 0)})
            
            rows.append({"Particulars": "  Changes in Working Capital:", "Amount": ""})
            
            if abs(cf['details'].get('inventory_change', 0)) > 0.001:
                rows.append({"Particulars": "    (Increase)/Decrease in Inventory", "Amount": cf['details']['inventory_change']})
            if abs(cf['details'].get('receivables_change', 0)) > 0.001:
                rows.append({"Particulars": "    (Increase)/Decrease in Receivables", "Amount": cf['details']['receivables_change']})
            if abs(cf['details'].get('payables_change', 0)) > 0.001:
                rows.append({"Particulars": "    Increase/(Decrease) in Payables", "Amount": cf['details']['payables_change']})
            if abs(cf['details'].get('other_liabilities_change', 0)) > 0.001:
                rows.append({"Particulars": "    Increase/(Decrease) in Other Liabilities", "Amount": cf['details']['other_liabilities_change']})
            
            rows.append({"Particulars": "", "Amount": ""})
            
            rows.append({"Particulars": "Cash Flow from Investing Activities", "Amount": cf['investing']})
            rows.append({"Particulars": "  Purchase/Sale of Fixed Assets", "Amount": cf['details'].get('fixed_assets_change', 0)})
            rows.append({"Particulars": "", "Amount": ""})
            
            rows.append({"Particulars": "Cash Flow from Financing Activities", "Amount": cf['financing']})
            rows.append({"Particulars": "  Changes in Capital/Loans", "Amount": cf['details'].get('capital_change', 0)})
            rows.append({"Particulars": "", "Amount": ""})
            
            rows.append({"Particulars": "Net Increase/Decrease in Cash", "Amount": cf['net_change']})
            
            return create_excel_response(pd.DataFrame(rows), f"Cash_Flow_{from_date}_{to_date}")

        else:
            return "Unknown report type", 400

    except Exception as e:
        print(f"Export Error: {e}")
        return str(e), 500



