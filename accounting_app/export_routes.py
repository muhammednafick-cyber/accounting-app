from flask import Blueprint, request, send_file, jsonify
from flask_login import login_required
import io
import pandas as pd
import sqlite3

from database import (
    get_ledger_details,
    get_inventory_details,
    get_ledger_transactions,
    get_trial_balance_data,
    get_stock_movement_data,
    get_balance_sheet_data,
    get_profit_and_loss_data,
    get_closing_inventory_data,
    get_voucher_register_data,
)
from .models import parse_date, format_date
from . import get_db_connection

export_bp = Blueprint("export_bp", __name__)


@export_bp.route("/export_ledger")
@login_required
def export_ledger():
    try:
        ledgers = get_ledger_details()
        df = pd.DataFrame(
            ledgers,
            columns=[
                "Ledger Code",
                "Ledger Name",
                "Group Code",
                "Group Name",
                "Nature",
                "Opening Balance",
                "Opening Balance Type",
                "Closing Balance",
            ],
        )
        output = io.BytesIO()
        df.to_excel(output, index=False)
        output.seek(0)
        return send_file(
            output,
            download_name="ledger_details.xlsx",
            as_attachment=True,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        print(f"Error in export_ledger: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500


@export_bp.route("/export_inventory")
@login_required
def export_inventory():
    try:
        inventory = get_inventory_details()
        df = pd.DataFrame(
            inventory,
            columns=[
                "Item Code",
                "Item Name",
                "Group Code",
                "Group Name",
                "Unit",
                "Selling Price",
                "Opening Quantity",
                "VAT %",
                "Opening Price (Cost)",
                "Location",
            ],
        )
        df["VAT %"] = df["VAT %"].apply(lambda x: float(x or 0))
        output = io.BytesIO()
        df.to_excel(output, index=False)
        output.seek(0)
        print(f"Exported {len(inventory)} inventory items with full columns")
        return send_file(
            output,
            download_name="inventory_details.xlsx",
            as_attachment=True,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        print(f"Error in export_inventory: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500


@export_bp.route("/export_report/ledger_transactions")
@login_required
def export_ledger_transactions():
    ledger_name = request.args.get("ledger_name")
    from_date = parse_date(request.args.get("from_date"))
    to_date = parse_date(request.args.get("to_date"))
    try:
        transactions, _closing_balance = get_ledger_transactions(ledger_name, from_date, to_date)
        # Format dates
        formatted_transactions = []
        for t in transactions:
            # t is a dict: {'date': ..., 'voucher_number': ..., 'voucher_type': ..., 'narration': ..., 'debit': ..., 'credit': ..., 'balance': ...}
            formatted_transactions.append([
                t['voucher_number'],
                t['voucher_type'],
                format_date(t['date']),
                t['narration'],
                t['debit'],
                t['credit'],
                t['balance']
            ])
            
        df = pd.DataFrame(formatted_transactions, columns=["Voucher Number", "Voucher Type", "Date", "Narration", "Debit", "Credit", "Balance"])
        output = io.BytesIO()
        df.to_excel(output, index=False)
        output.seek(0)
        return send_file(output, download_name=f"{ledger_name}_transactions.xlsx", as_attachment=True)
    except Exception as e:
        print(f"Error in export_ledger_transactions: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500


@export_bp.route("/export_report/trial_balance")
@login_required
def export_trial_balance():
    try:
        trial_balance = get_trial_balance_data()
        data = []
        for group in trial_balance:
            data.append([group["group_name"], "", ""])
            for ledger in group["ledgers"]:
                data.append(["", ledger["ledger_name"], round(ledger["debit"], 2), round(ledger["credit"], 2)])
            data.append(["Total " + group["group_name"], "", round(group["total_debit"], 2), round(group["total_credit"], 2)])
            data.append([])
        df = pd.DataFrame(data, columns=["Group", "Ledger Name", "Debit", "Credit"])
        df = df.dropna(how="all")
        output = io.BytesIO()
        df.to_excel(output, index=False)
        output.seek(0)
        return send_file(output, download_name="trial_balance.xlsx", as_attachment=True)
    except Exception as e:
        print(f"Error in export_trial_balance: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500


@export_bp.route("/export_report/stock_movement")
@login_required
def export_stock_movement():
    item_name = request.args.get("item_name")
    from_date = parse_date(request.args.get("from_date"))
    to_date = parse_date(request.args.get("to_date"))
    try:
        movement = get_stock_movement_data(item_name, from_date, to_date)
        # movement tuples: (vn, dt, vt, qty_in, qty_out, running_qty, wap, running_val)
        
        formatted_movement = []
        for m in movement:
            fm = list(m)
            fm[1] = format_date(fm[1]) # Format date at index 1
            formatted_movement.append(fm)
            
        df = pd.DataFrame(formatted_movement, columns=[
            "Voucher Number", "Date", "Voucher Type",
            "Inward Qty", "Outward Qty",
            "Closing Qty", "WAP", "Closing Value", "Location"
        ])

        df = df[[
            "Date", "Voucher Type", "Voucher Number", "Location",
            "Inward Qty", "Outward Qty",
            "Closing Qty", "WAP", "Closing Value"
        ]]
        
        output = io.BytesIO()
        df.to_excel(output, index=False)
        output.seek(0)
        return send_file(output, download_name=f"{item_name}_stock_movement.xlsx", as_attachment=True)
    except Exception as e:
        print(f"Error in export_stock_movement: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500


@export_bp.route("/export_report/balance_sheet")
@login_required
def export_balance_sheet():
    as_of_date = parse_date(request.args.get("as_of_date"))
    try:
        balance_sheet, total_assets, total_liabilities = get_balance_sheet_data(as_of_date)
        data = []
        
        # Assets
        data.append(["Assets", "", ""])
        for group_name, ledgers in balance_sheet["assets"].items():
            data.append([group_name, "", ""])
            for ledger in ledgers:
                data.append(["", ledger["ledger_name"], ledger["amount"]])
            group_total = round(sum(l['amount'] for l in ledgers), 2)
            data.append(["Total " + group_name, "", group_total])
            data.append([])
        data.append(["Total Assets", "", total_assets])
        
        data.append([])
        
        # Liabilities
        data.append(["Liabilities & Capital", "", ""])
        for group_name, ledgers in balance_sheet["liabilities"].items():
            data.append([group_name, "", ""])
            for ledger in ledgers:
                data.append(["", ledger["ledger_name"], ledger["amount"]])
            group_total = round(sum(l['amount'] for l in ledgers), 2)
            data.append(["Total " + group_name, "", group_total])
            data.append([])
        data.append(["Total Liabilities & Capital", "", total_liabilities])

        df = pd.DataFrame(data, columns=["Group", "Ledger Name", "Amount"])
        output = io.BytesIO()
        df.to_excel(output, index=False)
        output.seek(0)
        return send_file(output, download_name="balance_sheet.xlsx", as_attachment=True)
    except Exception as e:
        print(f"Error in export_balance_sheet: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500


@export_bp.route("/export_report/profit_and_loss")
@login_required
def export_profit_and_loss():
    from_date = parse_date(request.args.get("from_date"))
    to_date = parse_date(request.args.get("to_date"))
    try:
        profit_and_loss, total_income, total_expenses, net_profit = get_profit_and_loss_data(from_date, to_date)
        data = []
        data.append(["Income", "", ""])
        for ledger in profit_and_loss["income"]:
            data.append(["", ledger["ledger_name"], ledger["amount"]])
        data.append(["Total Income", "", total_income])
        data.append([])
        data.append(["Expenses", "", ""])
        for ledger in profit_and_loss["expenses"]:
            data.append(["", ledger["ledger_name"], ledger["amount"]])
        data.append(["Total Expenses", "", total_expenses])
        data.append(["Net Profit/Loss", "", net_profit])

        df = pd.DataFrame(data, columns=["Category", "Ledger Name", "Amount"])
        output = io.BytesIO()
        df.to_excel(output, index=False)
        output.seek(0)
        return send_file(output, download_name="profit_and_loss.xlsx", as_attachment=True)
    except Exception as e:
        print(f"Error in export_profit_and_loss: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500


@export_bp.route("/export_report/closing_inventory")
@login_required
def export_closing_inventory():
    as_of_date = parse_date(request.args.get("as_of_date"))
    try:
        closing_inventory, total_cost_amount = get_closing_inventory_data(as_of_date)
        df = pd.DataFrame(
            closing_inventory,
            columns=["item_code", "item_name", "group_name", "location_name", "quantity", "wap", "cost_amount"],
        )
        df.columns = ["Item Code", "Item Name", "Item Group", "Location", "Quantity", "WAP", "Cost Amount"]
        df.loc[len(df)] = ["Total", "", "", "", "", "", total_cost_amount]
        output = io.BytesIO()
        df.to_excel(output, index=False)
        output.seek(0)
        return send_file(output, download_name="closing_inventory.xlsx", as_attachment=True)
    except Exception as e:
        print(f"Error in export_closing_inventory: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500


@export_bp.route("/export_report/voucher_register")
@login_required
def export_voucher_register():
    voucher_type = request.args.get("voucher_type")
    from_date = parse_date(request.args.get("from_date"))
    to_date = parse_date(request.args.get("to_date"))
    
    try:
        # get_voucher_register_data returns list of vouchers with items
        vouchers = get_voucher_register_data(voucher_type, from_date, to_date)
        
        # Flatten for Excel
        flattened_data = []
        
        for v in vouchers:
            # v keys: voucher_number, date, amount, narration, party_name, items, voucher_type
            v_date = format_date(v['date'])
            v_no = v['voucher_number']
            v_type = v['voucher_type']
            party = v['party_name']
            narration = v['narration']
            
            # Check if items exist and is a list
            if v.get('items') and isinstance(v['items'], list) and len(v['items']) > 0:
                for item in v['items']:
                    flattened_data.append([
                        v_date,
                        v_no,
                        v_type,
                        party,
                        item.get('name', ''),
                        item.get('qty', 0),
                        item.get('rate', 0),
                        item.get('amount', 0),
                        narration
                    ])
            else:
                # If no items (e.g. accounting voucher only?), still show row
                flattened_data.append([
                    v_date,
                    v_no,
                    v_type,
                    party,
                    "", # Item Name
                    0,  # Qty
                    0,  # Rate
                    v['amount'], # Amount
                    narration
                ])
                
        df = pd.DataFrame(flattened_data, columns=[
            "Date", "Voucher No", "Type", "Party", 
            "Item Name", "Quantity", "Rate", "Amount", "Narration"
        ])
        
        output = io.BytesIO()
        df.to_excel(output, index=False)
        output.seek(0)
        
        filename = f"{voucher_type}_Register.xlsx"
        return send_file(
            output, 
            download_name=filename, 
            as_attachment=True,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
    except Exception as e:
        print(f"Error in export_voucher_register: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500
