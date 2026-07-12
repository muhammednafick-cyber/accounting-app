import io

import xlsxwriter
from flask import send_file
from flask_login import login_required

from . import import_bp


@import_bp.route("/download_voucher_template/<voucher_type>", methods=["GET"])
@login_required
def download_voucher_template(voucher_type):
    # Returns must be entered manually by pulling the source Sales/Purchase voucher
    if voucher_type in ("Sales Return", "Purchase Return"):
        return (
            f"{voucher_type} vouchers cannot be imported from Excel. "
            "Please enter them manually by pulling the original voucher.",
            400,
        )
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    worksheet = workbook.add_worksheet(voucher_type)

    # Define headers based on voucher type
    if voucher_type == "Sales":
        headers = [
            "Voucher Group ID",
            "Date",
            "Narration",
            "Party Ledger Name",
            "Sales Ledger",
            "Item Name",
            "Quantity",
            "Rate",
            "VAT %",
            "Discount Amount",
            "Cost Center"
        ]
    elif voucher_type == "Purchase":
        headers = [
            "Voucher Group ID",
            "Date",
            "Narration",
            "Party Ledger Name",
            "Item Name",
            "Quantity",
            "Rate",
            "VAT %",
            "Discount Amount",
            "Cost Center",
            "Reference Number",
            "Invoice Date",
            "Weight (KG)"
        ]
    elif voucher_type == "Additional Charge":
        headers = [
            "Voucher Group ID",
            "Date",
            "Narration",
            "Linked Purchase Voucher",
            "Party Ledger Name",
            "Amount",
            "Valuation Method", # Value / Quantity / Weight (KG)
            "VAT Amount"
        ]
    elif voucher_type == "Service Income":
        headers = [
            "Voucher Group ID",
            "Date",
            "Narration",
            "Ledger Name",
            "Amount",
            "Type",
            "VAT %",
            "Cost Center"
        ]
    elif voucher_type == "Inventory Transfer":
        headers = [
            "Voucher Group ID",
            "Date",
            "Narration",
            "Item Name",
            "Quantity",
            "From Location",
            "To Location",
            "Cost Center"
        ]
    elif voucher_type == "Stock Adjustment":
        headers = [
            "Voucher Group ID",
            "Date",
            "Narration",
            "Ledger Name",
            "Type",
            "Item Name",
            "Quantity",
            "Cost Center"
        ]
    elif voucher_type == "Expense":
        headers = [
            "Voucher Group ID",
            "Date",
            "Narration",
            "Ledger Name",
            "Amount",
            "Type", # Dr/Cr
            "VAT %",
            "Cost Center",
            "Reference Number",
            "Invoice Date"
        ]
    elif voucher_type in ["Journal", "Service Income"]:
        headers = [
            "Voucher Group ID",
            "Date",
            "Narration",
            "Ledger Name",
            "Amount",
            "Type", # Dr/Cr
            "VAT %",
            "Cost Center"
        ]

    else:
        # Receipt, Payment, Contra, etc.
        headers = [
            "Voucher Group ID",
            "Date",
            "Narration",
            "Ledger Name",
            "Amount",
            "Type", # Dr/Cr
            "Cost Center"
        ]

    # Write headers
    bold = workbook.add_format({'bold': True})
    for col, header in enumerate(headers):
        worksheet.write(0, col, header, bold)
        worksheet.set_column(col, col, 15) # Set default width

    # Add sample data (Row 2, index 1)
    sample_data = []
    
    if voucher_type == "Sales":
        sample_data = [
            "1", "2023-12-31", "Sales Invoice #101", "Customer A", "Sales Account",
            "Item X", 10, 100, 5, 0, "Project A"
        ]
    elif voucher_type == "Purchase":
        sample_data = [
            "1", "2023-12-31", "Purchase Bill #55", "Supplier B",
            "Item Y", 20, 50, 5, 0, "Project B", "INV-55", "2023-12-30", 15.5
        ]
    elif voucher_type == "Additional Charge":
        sample_data = [
            "1", "2023-12-31", "Freight Charges", "PUR-00001", "Transporter A",
            100, "Value", 5
        ]
    elif voucher_type == "Service Income":
        sample_data = [
            "1", "2023-12-31", "Consulting Fee", "Service Revenue Account",
            1000, "Credit", 5, "Project A"
        ]
    elif voucher_type == "Inventory Transfer":
        sample_data = [
            "1", "2023-12-31", "Stock Transfer", 
            "Item X", 5, "Warehouse 1", "Shop 1", "Project A"
        ]
        # Note: Item Name is index 3 in header list for this type?
        # Headers: ID, Date, Narration, Item Name, Qty, Rate, From, To, Cost Center
        # My sample above has empty string for Ledger Name which doesn't exist in this header list.
        # Correcting sample mapping below.
    elif voucher_type == "Stock Adjustment":
        sample_data = [
            "1", "2023-12-31", "Stock Taking", "Stock Loss Account", "Debit",
            "Item Z", 2, "Project A"
        ]
        # Note: Type Debit = Expense (Stock Out), Type Credit = Income (Stock In)
    elif voucher_type == "Expense":
         sample_data = [
            "1", "2023-12-31", "Office Rent", "Rent Expense",
            1000, "Debit", 5, "Project A", "INV-101", "2023-12-30"
        ]
    elif voucher_type == "Service Income":
         sample_data = [
            "1", "2023-12-31", "Consulting", "Service Income Ledger",
            2000, "Credit", 5, "Project A"
        ]
    elif voucher_type == "Receipt":
         sample_data = [
            "1", "2023-12-31", "Customer Payment", "Customer A",
            5000, "Credit", "Project A"
        ]
    elif voucher_type == "Payment":
         sample_data = [
            "1", "2023-12-31", "Vendor Payment", "Supplier B",
            2000, "Debit", "Project A"
        ]
    elif voucher_type == "Journal":
         sample_data = [
            "1", "2023-12-31", "Adjustment", "Expense Account",
            500, "Debit", 5, "Project A"
        ]
         # Maybe add second row for Journal?
    else:
         # Generic fallback
         sample_data = ["1", "2023-12-31", "Narration", "Party Ledger Name", 100, "Debit", "Cost Center"]

    # Correct specific mappings based on headers
    if voucher_type == "Inventory Transfer":
        # Headers: ID, Date, Narration, Item Name, Qty, From, To, Cost Center
        sample_data = [
            "1", "2023-12-31", "Transfer Note", 
            "Item X", 5, "Warehouse A", "Shop B", "Project A"
        ]
    
    # Write sample row
    if sample_data:
        # Format dates if needed, but writing string is safer for template
        for col, data in enumerate(sample_data):
            if col < len(headers):
                worksheet.write(1, col, data)
        
        # Add a second row for Journal to show balancing?
        if voucher_type == "Journal":
             sample_data_2 = ["1", "2023-12-31", "Adjustment", "Cash Account", 500, "Credit", 0, "Project A"]
             for col, data in enumerate(sample_data_2):
                if col < len(headers):
                    worksheet.write(2, col, data)

    workbook.close()
    output.seek(0)
    print(f"Downloaded template for {voucher_type}")
    return send_file(
        output,
        download_name=f"{voucher_type}_Template.xlsx",
        as_attachment=True,
    )


@import_bp.route("/download_group_template", methods=["GET"])
@login_required
def download_group_template():
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    worksheet = workbook.add_worksheet("Groups")
    headers = ["Group Name", "Nature"]
    for col, header in enumerate(headers):
        worksheet.write(0, col, header)
    workbook.close()
    output.seek(0)
    print("Downloaded group template")
    return send_file(
        output,
        download_name="Group_Template.xlsx",
        as_attachment=True,
    )


@import_bp.route("/download_sub_group_template", methods=["GET"])
@login_required
def download_sub_group_template():
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    worksheet = workbook.add_worksheet("Sub Groups")
    headers = ["Parent Group Name", "Sub Group Name"]
    for col, header in enumerate(headers):
        worksheet.write(0, col, header)
    workbook.close()
    output.seek(0)
    print("Downloaded sub group template")
    return send_file(
        output,
        download_name="Sub_Group_Template.xlsx",
        as_attachment=True,
    )


@import_bp.route("/download_ledger_template", methods=["GET"])
@login_required
def download_ledger_template():
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    worksheet = workbook.add_worksheet("Ledgers")
    headers = [
        "Ledger Code",
        "Ledger Name",
        "Group Name",
        "Opening Balance",
        "Opening Balance Type",
        "Opening Balance Date", # Mandatory (DD-MM-YYYY or YYYY-MM-DD)
        "Sub Group Name" # Optional
    ]
    for col, header in enumerate(headers):
        worksheet.write(0, col, header)
    workbook.close()
    output.seek(0)
    print("Downloaded ledger template")
    return send_file(
        output,
        download_name="Ledger_Template.xlsx",
        as_attachment=True,
    )


@import_bp.route("/download_cost_center_template", methods=["GET"])
@login_required
def download_cost_center_template():
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    worksheet = workbook.add_worksheet("Cost Centers")
    headers = ["Cost Center Code", "Cost Center Name"]
    for col, header in enumerate(headers):
        worksheet.write(0, col, header)
    workbook.close()
    output.seek(0)
    print("Downloaded cost center template")
    return send_file(
        output,
        download_name="Cost_Center_Template.xlsx",
        as_attachment=True,
    )


@import_bp.route("/download_inventory_group_template", methods=["GET"])
@login_required
def download_inventory_group_template():
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    worksheet = workbook.add_worksheet("Inventory Groups")
    headers = ["Group Code", "Group Name"]
    for col, header in enumerate(headers):
        worksheet.write(0, col, header)
    workbook.close()
    output.seek(0)
    print("Downloaded inventory group template")
    return send_file(
        output,
        download_name="Inventory_Group_Template.xlsx",
        as_attachment=True,
    )


# FULLY UPDATED INVENTORY TEMPLATE WITH ALL 7 COLUMNS (VAT INCLUDED)
@import_bp.route("/download_inventory_template", methods=["GET"])
@login_required
def download_inventory_template():
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    worksheet = workbook.add_worksheet("Inventory")

    # Includes Purchase Date and Balancing Ledger for Opening Balance.
    # No Location column: opening stock lands on the active location.
    headers = [
        "Item Code",
        "Item Name",
        "Group Name",
        "Unit",
        "VAT %",
        "Opening Quantity",            # Optional
        "Opening Price (Cost)",        # Optional
        "Purchase Date",               # Optional - per-item opening date (DD-MM-YYYY)
        "Balancing Ledger",            # Optional - ledger to credit for double-entry
        "Selling Price"                # Optional - creates/updates Selling Price Master
    ]

    # Write headers
    bold = workbook.add_format({'bold': True})
    header_fmt = workbook.add_format({'bold': True, 'bg_color': '#2563AB', 'font_color': '#FFFFFF'})
    note_fmt = workbook.add_format({'italic': True, 'font_color': '#666666'})
    for col, header in enumerate(headers):
        worksheet.write(0, col, header, header_fmt)

    # Column widths
    worksheet.freeze_panes(1, 0)
    worksheet.set_column(0, 0, 15)   # Item Code
    worksheet.set_column(1, 1, 25)   # Item Name
    worksheet.set_column(2, 2, 20)   # Group Name
    worksheet.set_column(3, 3, 10)   # Unit
    worksheet.set_column(4, 4, 10)   # VAT %
    worksheet.set_column(5, 5, 16)   # Opening Quantity
    worksheet.set_column(6, 6, 18)   # Opening Price (Cost)
    worksheet.set_column(7, 7, 15)   # Purchase Date
    worksheet.set_column(8, 8, 25)   # Balancing Ledger
    worksheet.set_column(9, 9, 14)   # Selling Price

    # Data validations
    last_row = 1001

    worksheet.data_validation(1, 4, last_row, 4, {
        'validate': 'decimal', 'criteria': 'between', 'minimum': 0, 'maximum': 100,
        'input_title': 'VAT %', 'input_message': 'Enter a number between 0 and 100'
    })
    worksheet.data_validation(1, 5, last_row, 5, {
        'validate': 'decimal', 'criteria': '>=', 'value': 0,
        'input_title': 'Opening Quantity', 'input_message': 'Enter a number >= 0 (optional)'
    })
    worksheet.data_validation(1, 6, last_row, 6, {
        'validate': 'decimal', 'criteria': '>=', 'value': 0,
        'input_title': 'Opening Price (Cost)', 'input_message': 'Enter a number >= 0 (optional)'
    })
    worksheet.data_validation(1, 7, last_row, 7, {
        'validate': 'any',
        'input_title': 'Purchase Date', 'input_message': 'DD-MM-YYYY. Required if Opening Quantity > 0; leave blank otherwise.'
    })
    worksheet.data_validation(1, 8, last_row, 8, {
        'validate': 'any',
        'input_title': 'Balancing Ledger', 'input_message': 'Exact ledger name to credit. Required if Opening Quantity > 0.'
    })
    worksheet.data_validation(1, 9, last_row, 9, {
        'validate': 'decimal', 'criteria': '>=', 'value': 0,
        'input_title': 'Selling Price', 'input_message': 'Enter a number >= 0 (optional). Saved to Selling Price Master.'
    })

    # Example rows
    example_data = [
        ["IT001", "Tomato", "VEGETABLES", "KG", 5, 10, 40.00, "01-01-2024", "Opening Stock Account", 55.00],
        ["IT002", "Rice",   "GROCERY",    "KG", 0, 20,  35.00, "01-01-2024", "Opening Stock Account", 42.50],
        ["IT003", "Milk",   "DAIRY",      "LTR",5,  0,   "",   "",           "",                       ""],
    ]

    for row_num, row_data in enumerate(example_data, start=1):
        for col_num, cell_data in enumerate(row_data):
            worksheet.write(row_num, col_num, cell_data)

    # Instructions row
    worksheet.write(len(example_data) + 2, 0, "Notes:", bold)
    worksheet.write(len(example_data) + 3, 0, "Purchase Date: DD-MM-YYYY format. Leave blank to use company fiscal year start.", note_fmt)
    worksheet.write(len(example_data) + 4, 0, "Balancing Ledger: Exact ledger name to credit (e.g. 'Opening Stock Account'). Leave blank to skip credit entry.", note_fmt)
    worksheet.write(len(example_data) + 5, 0, "Selling Price: Optional. If filled, the Selling Price Master is created/updated for the item. Leave blank to skip.", note_fmt)
    worksheet.write(len(example_data) + 6, 0, "Location: Opening stock is saved against the active location (default location if not switched). Re-upload with another active location to add openings there.", note_fmt)

    workbook.close()
    output.seek(0)
    print("Downloaded FULL inventory template with 10 columns (Purchase Date + Balancing Ledger)")
    return send_file(
        output,
        download_name="Inventory_Template.xlsx",
        as_attachment=True,
    )


@import_bp.route("/download_credit_terms_template", methods=["GET"])
@login_required
def download_credit_terms_template():
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    worksheet = workbook.add_worksheet("Credit Terms")
    headers = ["Ledger Name", "Credit Days"]
    
    bold = workbook.add_format({'bold': True})
    for col, header in enumerate(headers):
        worksheet.write(0, col, header, bold)
        worksheet.set_column(col, col, 20)

    # Example data
    worksheet.write(1, 0, "Customer A")
    worksheet.write(1, 1, 30)

    workbook.close()
    output.seek(0)
    print("Downloaded credit terms template")
    return send_file(
        output,
        download_name="Credit_Terms_Template.xlsx",
        as_attachment=True,
    )