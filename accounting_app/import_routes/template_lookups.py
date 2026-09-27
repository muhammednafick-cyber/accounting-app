"""Database-backed dropdowns for the Excel import templates.

Every template column that must match an existing record - a ledger, an item, a
cost centre, a location - gets a pick-list built from that company's own data
when the file is downloaded. You cannot type a value the import will reject,
and the valid values travel with the file.

The lists are written to a "Valid Values" sheet and referenced by range, not
inlined into the validation rule. Excel caps an inline list source at 255
characters, which a real chart of accounts blows past immediately; a range
reference has no such limit.
"""
from xlsxwriter.utility import xl_col_to_name

REFERENCE_SHEET = "Valid Values"
VALIDATION_ROWS = 2000     # how far down the sheet the dropdown applies

# Fixed vocabularies the application itself defines.
STATIC_LISTS = {
    "nature": ["Assets", "Liabilities", "Income", "Expenses"],
    "drcr": ["Debit", "Credit"],
    "valuation": ["Value", "Quantity", "Weight (KG)"],
    "yesno": ["Yes", "No"],
}

# What each lookup is called on the reference sheet.
LOOKUP_TITLES = {
    "ledger": "Ledger Name",
    "item": "Item Name",
    "group": "Account Group",
    "sub_group": "Sub Group",
    "stock_group": "Stock Group",
    "unit": "Unit",
    "cost_center": "Cost Centre",
    "location": "Location",
    "master_group": "Master Group",
    "purchase_voucher": "Purchase Voucher",
    "nature": "Nature",
    "drcr": "Debit / Credit",
    "valuation": "Valuation Method",
    "yesno": "Yes / No",
}

# Column heading -> lookup. Headings that mean different things in different
# templates (notably "Group Name") are resolved by the caller's `overrides`.
HEADER_LOOKUPS = {
    "party ledger name": "ledger",
    "ledger name": "ledger",
    "sales ledger": "ledger",
    "purchase ledger": "ledger",
    "balancing ledger": "ledger",
    "item name": "item",
    # Purchase templates split the item column in two: the system name must
    # match an existing item, the extracted (vendor's) name is free text.
    "system item name": "item",
    "parent group name": "group",
    "sub group name": "sub_group",
    "unit": "unit",
    "cost center": "cost_center",
    "cost centre": "cost_center",
    "location": "location",
    "from location": "location",
    "to location": "location",
    "master group": "master_group",
    "linked purchase voucher": "purchase_voucher",
    "nature": "nature",
    "type": "drcr",
    "opening balance type": "drcr",
    "valuation method": "valuation",
}


def _column(cursor, sql, params):
    try:
        cursor.execute(sql, params)
        seen, out = set(), []
        for row in cursor.fetchall():
            value = row[0]
            if value is None:
                continue
            value = str(value).strip()
            if value and value not in seen:
                seen.add(value)
                out.append(value)
        return out
    except Exception as exc:
        print(f"[template] lookup failed: {exc}")
        return []


def load_lookups(company_id, needed):
    """{lookup_key: [values]} for the lookups a template actually uses."""
    values = {key: list(STATIC_LISTS[key])
              for key in needed if key in STATIC_LISTS}

    db_needed = [k for k in needed if k not in STATIC_LISTS]
    if not db_needed or not company_id:
        return values

    from database.config import get_connection
    queries = {
        "ledger": ("SELECT ledger_name FROM ledgers WHERE company_id = %s "
                   "AND COALESCE(is_active, 1) = 1 ORDER BY ledger_name"),
        "item": ("SELECT name FROM inventory WHERE company_id = %s "
                 "AND COALESCE(is_active, 1) = 1 ORDER BY name"),
        "group": ("SELECT group_name FROM groups WHERE company_id = %s "
                  "ORDER BY group_name"),
        "sub_group": ("SELECT sub_group_name FROM sub_groups "
                      "WHERE company_id = %s ORDER BY sub_group_name"),
        "stock_group": ("SELECT group_name FROM inventory_groups "
                        "WHERE company_id = %s ORDER BY group_name"),
        "unit": ("SELECT unit_code FROM units WHERE company_id = %s "
                 "ORDER BY unit_code"),
        "cost_center": ("SELECT center_name FROM cost_centers "
                        "WHERE company_id = %s AND COALESCE(is_active, 1) = 1 "
                        "ORDER BY center_name"),
        "location": ("SELECT location_name FROM locations WHERE company_id = %s "
                     "AND COALESCE(is_active, 1) = 1 ORDER BY location_name"),
        "master_group": ("SELECT master_group_name FROM master_groups "
                         "WHERE company_id = %s ORDER BY master_group_name"),
        # Recent purchases only - an additional charge is linked to a bill you
        # just entered, and every voucher ever raised is not a usable list.
        "purchase_voucher": ("SELECT voucher_number FROM vouchers "
                             "WHERE company_id = %s AND voucher_type = 'Purchase' "
                             "ORDER BY date DESC, voucher_number DESC LIMIT 500"),
    }

    conn = get_connection()
    try:
        cursor = conn.cursor()
        for key in db_needed:
            sql = queries.get(key)
            if sql:
                values[key] = _column(cursor, sql, (company_id,))
    finally:
        conn.close()
    return values


def apply_lookups(workbook, worksheet, headers, company_id, overrides=None,
                  first_row=1):
    """Attach a dropdown to every template column backed by a lookup.

    `headers` is the list of column headings in order. `overrides` maps a
    heading to a lookup key where the heading alone is ambiguous - "Group Name"
    means account groups on the ledger template and stock groups on the item
    template.

    Returns the number of columns that got a dropdown.
    """
    overrides = {k.strip().lower(): v for k, v in (overrides or {}).items()}

    # Which lookup, if any, belongs to each column
    columns = {}
    for index, header in enumerate(headers):
        key = str(header).strip().lower()
        lookup = overrides.get(key, HEADER_LOOKUPS.get(key))
        if lookup:
            columns[index] = lookup
    if not columns:
        return 0

    values = load_lookups(company_id, set(columns.values()))

    # One column per lookup on the reference sheet
    reference = workbook.add_worksheet(REFERENCE_SHEET)
    title_fmt = workbook.add_format({
        "bold": True, "bg_color": "#2563AB", "font_color": "#FFFFFF"})
    reference.set_column(0, 20, 26)

    ranges = {}
    for position, lookup in enumerate(sorted(set(columns.values()))):
        entries = values.get(lookup) or []
        reference.write(0, position, LOOKUP_TITLES.get(lookup, lookup), title_fmt)
        for row, entry in enumerate(entries, start=1):
            reference.write(row, position, entry)
        if not entries:
            reference.write(1, position, "(none set up yet)")
            continue
        letter = xl_col_to_name(position)
        ranges[lookup] = (f"='{REFERENCE_SHEET}'!${letter}$2:"
                          f"${letter}${len(entries) + 1}")

    applied = 0
    for index, lookup in columns.items():
        source = ranges.get(lookup)
        if not source:
            continue    # nothing defined yet - leave the column free to type in
        label = LOOKUP_TITLES.get(lookup, lookup)
        worksheet.data_validation(first_row, index, VALIDATION_ROWS, index, {
            "validate": "list",
            "source": source,
            "input_title": f"Pick a {label}"[:32],
            "input_message": f"Choose from the {label} list, or see the "
                             f"'{REFERENCE_SHEET}' sheet."[:255],
            "error_title": f"Unknown {label}"[:32],
            "error_message": (f"{label} must already exist in this company. "
                              f"The '{REFERENCE_SHEET}' sheet lists every "
                              f"valid entry.")[:255],
        })
        applied += 1
    return applied


# ---------------------------------------------------------------------------
# Ledger columns restricted by the Voucher Configuration
# ---------------------------------------------------------------------------
#
# A plain "Ledger Name" list offers every ledger in the company, on both sides.
# The import then refuses any line the Voucher Configuration or the built-in
# rules forbid - a Receipt debited to anything but Cash or Bank, say - so the
# sheet let you pick values it would reject. These columns get the same list
# the import accepts instead, taken from models.allowed_ledger_names.

# Ledger columns whose side is fixed by the voucher type.
FIXED_SIDE_COLUMNS = {
    ("Sales", "party ledger name"): "Debit",
    ("Sales", "sales ledger"): "Credit",
    ("Purchase", "party ledger name"): "Credit",
}


def restricted_ledger_columns(voucher_type, headers, company_id):
    """What to restrict, as a list of plans. Empty when nothing is restricted,
    and the ordinary all-ledgers dropdown is right."""
    from accounting_app.models import allowed_ledger_names

    lower = [str(h).strip().lower() for h in headers]
    plans = []

    # One ledger column whose side is chosen per row in the Type column.
    if "ledger name" in lower and "type" in lower:
        debit = allowed_ledger_names(voucher_type, "Debit", company_id=company_id)
        credit = allowed_ledger_names(voucher_type, "Credit", company_id=company_id)
        if debit is not None or credit is not None:
            plans.append({"kind": "by_type", "column": lower.index("ledger name"),
                          "type_column": lower.index("type"),
                          "Debit": debit, "Credit": credit,
                          "header": headers[lower.index("ledger name")]})

    for (vt, heading), side in FIXED_SIDE_COLUMNS.items():
        if vt == voucher_type and heading in lower:
            names = allowed_ledger_names(voucher_type, side, company_id=company_id)
            if names is not None:
                plans.append({"kind": "fixed", "column": lower.index(heading),
                              "side": side, "names": names,
                              "header": headers[lower.index(heading)]})
    return plans


def write_restricted_ledger_lists(workbook, worksheet, voucher_type, plans,
                                  company_id, first_row=1):
    """Put each restricted list on the reference sheet and point its column's
    dropdown at it. For a by-Type column the list follows that row's Type."""
    if not plans:
        return 0

    reference = workbook.get_worksheet_by_name(REFERENCE_SHEET)
    if reference is None:
        reference = workbook.add_worksheet(REFERENCE_SHEET)
        reference.set_column(0, 20, 26)
    title_fmt = workbook.add_format({
        "bold": True, "bg_color": "#245C56", "font_color": "#FFFFFF"})
    next_col = [0 if reference.dim_colmax is None else reference.dim_colmax + 1]
    all_ledgers = None

    def every_ledger():
        nonlocal all_ledgers
        if all_ledgers is None:
            all_ledgers = load_lookups(company_id, {"ledger"}).get("ledger") or []
        return all_ledgers

    def write_list(title, names, defined_name):
        col = next_col[0]
        next_col[0] += 1
        reference.write(0, col, title, title_fmt)
        entries = names or ["(none allowed - see Voucher Configuration)"]
        for row, value in enumerate(entries, start=1):
            reference.write(row, col, value)
        letter = xl_col_to_name(col)
        workbook.define_name(
            defined_name,
            f"='{REFERENCE_SHEET}'!${letter}$2:${letter}${len(entries) + 1}")

    applied = 0
    for plan in plans:
        column = plan["column"]
        if plan["kind"] == "by_type":
            debit = plan["Debit"] if plan["Debit"] is not None else every_ledger()
            credit = plan["Credit"] if plan["Credit"] is not None else every_ledger()
            either = sorted(set(debit) | set(credit))
            write_list(f"{voucher_type} Debit ledgers", debit, "LEDGERS_DEBIT")
            write_list(f"{voucher_type} Credit ledgers", credit, "LEDGERS_CREDIT")
            write_list(f"{voucher_type} ledgers (either side)", either, "LEDGERS_EITHER")
            type_cell = f"${xl_col_to_name(plan['type_column'])}{first_row + 1}"
            source = (f'=INDIRECT(IF({type_cell}="Credit","LEDGERS_CREDIT",'
                      f'IF({type_cell}="Debit","LEDGERS_DEBIT","LEDGERS_EITHER")))')
            message = (f"Choose the Type first: the ledgers allowed on the Debit "
                       f"and Credit side of a {voucher_type} differ.")
        else:
            name = "LEDGERS_" + plan["side"].upper() + "_" + str(column)
            write_list(f"{voucher_type} {plan['header']} ({plan['side']})",
                       plan["names"], name)
            source = "=" + name
            message = (f"Only the ledgers allowed on the {plan['side']} side of a "
                       f"{voucher_type}.")
        worksheet.data_validation(first_row, column, VALIDATION_ROWS, column, {
            "validate": "list",
            "source": source,
            "input_title": "Pick a Ledger"[:32],
            "input_message": message[:255],
            "error_title": "Ledger not allowed"[:32],
            "error_message": (f"That ledger is not allowed there on a "
                              f"{voucher_type} - see Setup > Voucher "
                              f"Configuration, or the '{REFERENCE_SHEET}' "
                              f"sheet for the lists.")[:255],
        })
        applied += 1
    return applied
