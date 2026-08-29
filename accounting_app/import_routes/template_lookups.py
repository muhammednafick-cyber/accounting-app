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
